import numpy as np
import torch
from tqdm import tqdm
import gc
from kneel.evaluation import assess_errors

from deeppipeline.kvs import GlobalKVS
from deeppipeline.common.core import mixup_pass

import cv2
import matplotlib.pyplot as plt


def pass_epoch(net, loader, optimizer, criterion):
    kvs = GlobalKVS()
    net.train(optimizer is not None)

    fold_id = kvs['cur_fold']
    epoch = kvs['cur_epoch']
    max_ep = kvs['args'].n_epochs

    running_loss = 0.0
    n_batches = len(loader)
    landmark_errors = {}
    device = next(net.parameters()).device
    pbar = tqdm(total=n_batches, ncols=200)

    with torch.set_grad_enabled(optimizer is not None):
        for i, entry in enumerate(loader):
            if optimizer is not None:
                optimizer.zero_grad()

            inputs = entry['img'].to(device)
            target = entry['kp_gt'].to(device).float()

            if kvs['args'].use_mixup and optimizer is not None:
                loss = mixup_pass(net, criterion, inputs, target, kvs['args'].mixup_alpha)
            else:
                outputs = net(inputs)
                loss = criterion(outputs, target)

            if optimizer is not None:
                loss.backward()
                optimizer.step()
                running_loss += loss.item()
                pbar.set_description(f"Fold [{fold_id}] [{epoch} | {max_ep}] | "
                                     f"Running loss {running_loss / (i + 1):.5f} / {loss.item():.5f}")
            else:
                running_loss += loss.item()
                pbar.set_description(desc=f"Fold [{fold_id}] [{epoch} | {max_ep}] | Validation progress")
            if optimizer is None:
                target_kp = entry['kp_gt'].numpy()
                h, w = inputs.size(2), inputs.size(3)
                if isinstance(outputs, tuple):
                    predicts = outputs[-1].to('cpu').numpy()
                else:
                    predicts = outputs.to('cpu').numpy()

                xy_batch = predicts
                xy_batch[:, :, 0] *= (w - 1)
                xy_batch[:, :, 1] *= (h - 1)

                target_kp = target_kp
                xy_batch = xy_batch

                target_kp[:, :, 0] *= (w - 1)
                target_kp[:, :, 1] *= (h - 1)

                for kp_id in range(target_kp.shape[1]):
                    spacing = getattr(kvs['args'], f"{kvs['args'].annotations}_spacing")
                    d = target_kp[:, kp_id] - xy_batch[:, kp_id]
                    err = np.sqrt(np.sum(d ** 2, 1)) * spacing
                    if kp_id not in landmark_errors:
                        landmark_errors[kp_id] = list()

                    landmark_errors[kp_id].append(err)

            pbar.update()
            gc.collect()
        gc.collect()
        pbar.close()

    if len(landmark_errors) > 0:
        for kp_id in landmark_errors:
            landmark_errors[kp_id] = np.hstack(landmark_errors[kp_id])
    else:
        landmark_errors = None

    return running_loss / n_batches, landmark_errors


def val_results_callback(writer, val_metrics, to_log, val_results):
    print(assess_errors(val_results))
