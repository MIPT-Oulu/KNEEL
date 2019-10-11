"""
    This micro-service takes a dicom image in and returns JSON with localized landmark coordinates.
    (c) Aleksei Tiulpin, University of Oulu, 2019
"""
import argparse
from flask import jsonify
from flask import Flask, request
from gevent.pywsgi import WSGIServer
from pydicom import dcmread
from pydicom.filebase import DicomBytesIO
import logging

from kneel.inference.pipeline import KneeAnnotatorPipeline

app = Flask(__name__)


# curl -F dicom=@01 -X POST http://127.0.0.1:5000/predict/bilateral
@app.route('/predict/bilateral', methods=['POST'])
def analyze_knee():
    loggers['kneel-backend:app'].info('Received DICOM')
    raw = DicomBytesIO(request.files['dicom'].read())
    data = dcmread(raw)
    loggers['kneel-backend:app'].info('DICOM read')
    landmarks = annotator.predict(data, args.roi_size_mm, args.pad, args.refine).squeeze()
    loggers['kneel-backend:app'].info('Prediction successful')
    if landmarks is not None:
        res = {'R': landmarks[0].tolist(), 'L': landmarks[1].tolist(), }
    else:
        res = {'R': None, 'L': None}
    loggers['kneel-backend:app'].info('Sending results back to the user')
    return jsonify(res)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--lc_snapshot_path', default='')
    parser.add_argument('--hc_snapshot_path', default='')
    parser.add_argument('--roi_size_mm', type=int, default=140)
    parser.add_argument('--pad', type=int, default=300)
    parser.add_argument('--device',  default='cuda')
    parser.add_argument('--refine', type=bool, default=False)
    parser.add_argument('--mean_std_path', default='')
    parser.add_argument('--deploy', type=bool, default=False)
    args = parser.parse_args()

    loggers = {}

    for logger_level in ['app', 'pipeline', 'roi-loc', 'landmarks-loc']:
        logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        logger = logging.getLogger(f'kneel-backend:{logger_level}')
        logger.setLevel(logging.DEBUG)

        loggers[f'kneel-backend:{logger_level}'] = logger

    annotator = KneeAnnotatorPipeline(args.lc_snapshot_path, args.hc_snapshot_path,
                                      args.mean_std_path, args.device, jit_trace=args.deploy, logger=loggers)

    if args.deploy:
        http_server = WSGIServer(('', 5000), app, log=logger)
        loggers['kneel-backend:app'].log(logging.INFO, 'Production server is running')
        http_server.serve_forever()
    else:
        loggers['kneel-backend:app'].log(logging.INFO, 'Debug server is running')
        app.run(host='', port=5000, debug=True)