import torch
import solt.data as sld
import numpy as np
import pydicom as dicom
from sas7bdat import SAS7BDAT
import pandas as pd
import cv2
from deeppipeline.common.transforms import numpy2tens
import matplotlib.pyplot as plt
import os


def convert_img(img):
    img = torch.from_numpy(img).float()
    if len(img.shape) == 2:
        img = img.unsqueeze(0)
    elif len(img.shape) == 3:
        img = img.transpose(0, 2).transpose(1, 2)

    return img


def read_pts(fname):
    with open(fname) as f:
        content = f.read()
    arr = np.array(list(map(lambda x: [float(x.split()[0]), float(x.split()[1])], content.split('\n')[3:-2])))
    return arr


def read_dicom(filename):
    """
    Reads a dicom file
    Parameters
    ----------
    filename : str or pydicom.dataset.FileDataset
        Full path to the image
    Returns
    -------
    out : tuple
        Image itself as uint16, spacing, and the DICOM metadata
    """

    if isinstance(filename, str):
        try:
            data = dicom.read_file(filename)
        except:
            return None
    elif isinstance(filename, dicom.dataset.FileDataset):
        data = filename

    img = np.frombuffer(data.PixelData, dtype=np.uint16).copy().astype(np.float64)

    if data.PhotometricInterpretation == 'MONOCHROME1':
        img = img.max() - img
    try:
        img = img.reshape((data.Rows, data.Columns))
    except:
        return None

    try:
        if isinstance(data.ImagerPixelSpacing, str):
            data.ImagerPixelSpacing = data.ImagerPixelSpacing.split()
    except:
        pass

    try:
        if isinstance(data.PixelSpacing, str):
            data.PixelSpacing = data.PixelSpacing.split()
    except:
        pass

    try:
        return img, float(data.ImagerPixelSpacing[0]), data
    except:
        pass
    try:
        return img, float(data.PixelSpacing[0]), data
    except:
        return None


def process_xray(img, cut_min=5, cut_max=99, multiplier=255):
    # This function changes the histogram of the image by doing global contrast normalization
    # cut_min - lowest percentile which is used to cut the image histogram
    # cut_max - highest percentile

    img = img.copy()
    lim1, lim2 = np.percentile(img, [cut_min, cut_max])
    img[img < lim1] = lim1
    img[img > lim2] = lim2

    img -= lim1
    img /= img.max()
    img *= multiplier

    return img


def parse_landmarks(txt):
    pts = np.array(list(map(int, txt.split(','))))
    return pts.reshape(pts.shape[0] // 2, 2)


def read_sas7bdata_pd(fname):
    data = []
    with SAS7BDAT(fname) as f:
        for row in f:
            data.append(row)

    return pd.DataFrame(data[1:], columns=data[0])


def l2m(lm, shape, sigma=1.5):
    # lm = (x,y)
    m = np.zeros(shape, dtype=np.uint8)

    if np.all(lm > 0) and lm[0] < shape[1] and lm[1] < shape[0]:
        x, y = np.meshgrid(np.linspace(-0.5, 0.5, m.shape[1]), np.linspace(-0.5, 0.5, m.shape[0]))
        mux = (lm[0]-m.shape[1]//2)/1./m.shape[1]
        muy = (lm[1]-m.shape[0]//2)/1./m.shape[0]
        s = sigma / 1. / m.shape[0]
        m = (x-mux)**2 / 2. / s**2 + (y-muy)**2 / 2. / s**2
        m = np.exp(-m)
        m -= m.min()
        m /= m.max()

    return m


def solt2torchhm(dc: sld.DataContainer, downsample=4, sigma=1.5):
    """
    Converts image and the landmarks in numpy into torch.
    The landmarks are converted into heatmaps as well.
    Covers both, low- and high-cost annotations cases.

    Parameters
    ----------
    dc : sld.DataContainer
        Data container
    downsample : int
        Downsampling factor to match the hourglass outputs.
    sigma : float
        Variance of the gaussian to fit at each landmark
    Returns
    -------
    out : tuple of torch.FloatTensor

    """
    if dc.data_format != 'IPL':
        raise TypeError('Invalid type of data container')

    img, landmarks, label = dc.data
    img = img.squeeze()
    h, w = img.shape[0], img.shape[1]
    target = None
    if sigma is not None:
        new_size = (h // downsample, w // downsample)

        target = []
        for i in range(landmarks.data.shape[0]):
            res = l2m(landmarks.data[i] // downsample, new_size, sigma)

            target.append(numpy2tens(res))

        target = torch.cat(target, 0).unsqueeze(0)
        assert target.size(0) == 1
        assert target.size(1) == landmarks.data.shape[0]
        assert target.size(2) == img.shape[0] // downsample
        assert target.size(3) == img.shape[1] // downsample
    # plt.figure()
    # plt.imshow(img.squeeze(), cmap=plt.cm.Greys_r)
    # plt.imshow(cv2.resize(target[0].squeeze().numpy(),
    #                       (img.shape[1], img.shape[0])),
    #            alpha=0.5, cmap=plt.cm.jet)
    # plt.show()
    img = convert_img(img)
    # the ground truth should stay in the image coordinate system.
    landmarks = torch.from_numpy(landmarks.data).float()
    landmarks[:, 0] /= w
    landmarks[:, 1] /= h

    return img, target, landmarks, label


def get_landmarks_from_hm(pred_map, resize_x, resize_y, pad, threshold=0.9):
    res = []

    for i in range(pred_map.shape[0]):
        try:
            m = pred_map[i, :, :]

            m -= m.min()
            m /= m.max()
            m *= 255
            m = m.astype(np.uint8)
            m = cv2.resize(m, (resize_x, resize_y))

            tmp = m.mean(0)
            tmp /= tmp.max()

            x = np.where(tmp > threshold)[0]  # coords
            ind = np.diff(x).argmax().astype(int)
            if ind == 0:
                x = int(np.median(x))
            else:
                x = int(np.median(x[:ind]))  # leftmost cluster
            tmp = m[:, x - pad:x + pad].mean(1)

            tmp[np.isnan(tmp)] = 0
            tmp /= tmp.max()
            y = np.where(tmp > threshold)  #
            y = y[0][0]
            res.append([x, y])
        except IndexError:
            res.append([-1, -1])

    return np.array(res)


def save_original_with_via_landmarks(subject_id, side, dicom_name, img_save_path, landmarks_dir):
    res = read_dicom(dicom_name)
    if res is None:
        return []
    img, spacing, _ = res
    img = process_xray(img).astype(np.uint8)
    if img.shape[0] == 0 or img.shape[1] == 0:
        return []

    cv2.imwrite(img_save_path, img)

    row, col = img.shape
    points = np.round(read_pts(os.path.join(landmarks_dir, '001.pts')) * 1 / spacing)
    landmarks_fl = points[list(range(12, 25, 2)), :]
    landmarks_tl = points[list(range(47, 64, 2)), :]

    points = np.round(read_pts(os.path.join(landmarks_dir, '001_f.pts')) * 1 / spacing)
    landmarks_fr = points[list(range(12, 25, 2)), :]
    landmarks_tr = points[list(range(47, 64, 2)), :]

    landmarks_fr[:, 0] = col - landmarks_fr[:, 0]
    landmarks_tr[:, 0] = col - landmarks_tr[:, 0]

    landmarks = {'TR': landmarks_tr, 'FR': landmarks_fr,
                 'TL': landmarks_tl, 'FL': landmarks_fl}

    result = []
    total_landmarks = sum([landmarks[key].shape[0] for key in landmarks])
    passed_through = 0

    for bone in ['T', 'F']:
        lndm = landmarks[bone + side]
        for pt_id in range(lndm.shape[0]):
            cx, cy = lndm[pt_id].astype(int)
            result.append([str(subject_id) + '.png',
                           os.path.getsize(img_save_path),
                           '{}', total_landmarks, passed_through + pt_id,
                           '{"name":"point","cx":' + str(cx) + ',"cy":' + str(cy) + '}',
                           '{"Bone": "' + bone + '","Side":"' + side + '"}'])
        passed_through += lndm.shape[0]

    return result, subject_id, spacing
