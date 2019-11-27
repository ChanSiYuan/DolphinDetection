#!/usr/bin/env python
# encoding: utf-8
"""
@author: Shanda Lau 刘祥德
@license: (C) Copyright 2019-now, Node Supply Chain Manager Corporation Limited.
@contact: shandalaulv@gmail.com
@software: 
@file: manager.py
@time: 2019/11/16 13:22
@version 1.0
@desc:
"""

from multiprocessing import Manager, Pool, Queue, cpu_count
from concurrent.futures import ThreadPoolExecutor, wait, ALL_COMPLETED
import interface as I
import stream
from config import VideoConfig
from detection.capture import *
from utils import *
from typing import List
from utils import clean_dir, logger
from detection import *
import cv2
import imutils
import traceback
import time
import ray


# Monitor will build multiple video stream receivers according the video configuration
class DetectionMonitor(object):

    def __init__(self, video_config_path: Path, stream_path: Path, sample_path: Path, frame_path: Path,
                 region_path: Path,
                 offline_path: Path = None, build_pool=True) -> None:
        super().__init__()
        # self.cfgs = I.load_video_config(video_config_path)[-1:]
        self.cfgs = I.load_video_config(video_config_path)
        self.cfgs = [c for c in self.cfgs if c.enable]

        # Communication Pipe between detector and stream receiver
        self.pipes = [Manager().Queue() for c in self.cfgs]
        self.stream_path = stream_path
        self.sample_path = sample_path
        self.frame_path = frame_path
        self.region_path = region_path
        self.offline_path = offline_path
        self.process_pool = None
        self.thread_pool = None
        if build_pool:
            self.process_pool = Pool(processes=cpu_count() - 1)
            self.thread_pool = ThreadPoolExecutor()
        self.clean()
        self.stream_receivers = [
            stream.StreamReceiver(self.stream_path / str(c.index), offline_path, c, self.pipes[idx]) for idx, c in
            enumerate(self.cfgs)]

    def monitor(self):
        self.call()
        self.wait()

    def call(self):
        for i, cfg in enumerate(self.cfgs):
            # clean all legacy streams and candidates files before initialization
            self.init_stream_receiver(i)
            self.init_detection(cfg, i)

    def wait(self):
        logger.info('Wait processes done.')
        if self.process_pool is not None:
            self.process_pool.close()
            self.process_pool.join()

        logger.info('Closed Pool')

    def init_detection(self, cfg, i):
        self.process_pool.apply_async(I.detect,
                                      (self.stream_path / str(cfg.index), self.region_path / str(cfg.index),
                                       self.pipes[i], cfg,))

    # def init_stream_receiver(self, cfg, i):
    #     self.process_pool.apply_async(I.read_stream, (self.stream_path / str(cfg.index), cfg, self.pipes[i],))
    def init_stream_receiver(self, i):
        return self.process_pool.apply_async(self.stream_receivers[i].receive_online)

    def clean(self):
        clean_dir(self.sample_path)
        clean_dir(self.stream_path)
        clean_dir(self.region_path)


# Base class embedded controllers of detector
# Each video has a detector controller
# But a controller will manager [row*col] concurrency threads or processes
# row and col are definied in video configuration
class EmbeddingControlMonitor(DetectionMonitor):
    def __init__(self, video_config_path: Path, stream_path: Path, sample_path: Path, frame_path: Path, region_path,
                 offline_path: Path = None, build_pool=False) -> None:
        super().__init__(video_config_path, stream_path, sample_path, frame_path, region_path, offline_path, build_pool)
        self.caps_queue = [Manager().Queue(maxsize=500) for c in self.cfgs]
        self.caps = []
        self.controllers = []

    def init_caps(self):
        for idx, c in enumerate(self.cfgs):
            if c.online:
                self.caps.append(
                    VideoOnlineSampleCapture(self.stream_path / str(c.index), self.sample_path / str(c.index),
                                             self.pipes[idx],
                                             self.caps_queue[idx],
                                             c, c.sample_rate))
            else:
                self.caps.append(
                    VideoOfflineRayCapture(self.stream_path / str(c.index), self.sample_path / str(c.index),
                                           self.offline_path,
                                           self.pipes[idx],
                                           self.caps_queue[idx], c, self.offline_path / str(c.index), c.sample_rate,
                                           delete_post=False))

    def call(self):

        self.init_caps()
        # Init stream receiver firstly, ensures video index that is arrived before detectors begin detection..
        for i, cfg in enumerate(self.cfgs):
            res = self.init_stream_receiver(i)
            # logger.debug(res.get())

        # Run video capture from stream
        for i in range(len(self.cfgs)):
            self.caps[i].read()

        # Init detector controller
        self.init_controllers()

    def init_controllers(self):
        pass

        # Concurrency based multi processes


class EmbeddingControlBasedProcessMonitor(EmbeddingControlMonitor):

    def __init__(self, video_config_path: Path, stream_path: Path, sample_path, frame_path, region_path: Path,
                 offline_path: Path = None) -> None:
        super().__init__(video_config_path, stream_path, sample_path, frame_path, region_path, offline_path)

    def init_controllers(self):
        self.controllers = [
            ProcessBasedDetectorController(cfg, self.stream_path / str(cfg.index), self.region_path,
                                           self.frame_path / str(cfg.index),
                                           self.caps_queue[idx],
                                           self.pipes[idx]
                                           ) for
            idx, cfg in enumerate(self.cfgs)]
        for i, cfg in enumerate(self.cfgs):
            logger.info('Init detector controller [{}]....'.format(cfg.index))
            res, detect_proc = self.controllers[i].start(self.process_pool)
            logger.info('Done init detector controller [{}]....'.format(cfg.index))

    # Concurrency based multi threads


class EmbeddingControlBasedThreadMonitor(EmbeddingControlMonitor):

    def __init__(self, video_config_path: Path, stream_path: Path, sample_path, frame_path, region_path: Path,
                 offline_path: Path = None) -> None:
        super().__init__(video_config_path, stream_path, sample_path, frame_path, region_path, offline_path)

    def init_controllers(self):
        self.controllers = [
            ProcessBasedDetectorController(cfg, self.stream_path / str(cfg.index), self.region_path,
                                           self.frame_path / str(cfg.index),
                                           self.caps_queue[idx],
                                           self.pipes[idx]
                                           ) for
            idx, cfg in enumerate(self.cfgs)]
        for i, cfg in enumerate(self.cfgs):
            logger.info('Init detector controller [{}]....'.format(cfg.index))
            self.thread_res = self.controllers[i].start(self.thread_pool)
            logger.info('Done init detector controller [{}]....'.format(cfg.index))

    def wait(self):
        super().wait()
        wait(self.thread_res, return_when=ALL_COMPLETED)

    # Concurrency based multiple threads and multiple processes


class EmbeddingControlBasedThreadAndProcessMonitor(EmbeddingControlMonitor):

    def __init__(self, video_config_path: Path, stream_path: Path, sample_path: Path, frame_path: Path,
                 region_path: Path,
                 offline_path=None) -> None:
        super().__init__(video_config_path, stream_path, sample_path, frame_path, region_path, offline_path)

    def init_controllers(self):
        self.controllers = [
            ProcessAndThreadBasedDetectorController(cfg, self.stream_path / str(cfg.index), self.region_path,
                                                    self.frame_path / str(cfg.index),
                                                    self.caps_queue[idx],
                                                    self.pipes[idx]
                                                    ) for
            idx, cfg in enumerate(self.cfgs)]
        for i, cfg in enumerate(self.cfgs):
            logger.info('Init detector controller [{}]....'.format(cfg.index))
            pool_res, thread_res = self.controllers[i].start([self.process_pool, self.thread_pool])
            self.thread_res = thread_res
            # logger.info(res.get())
            logger.info('Done init detector controller [{}]....'.format(cfg.index))

    def wait(self):
        super().wait()
        # wait all threads canceled in Thread Pool Executor
        wait(self.thread_res, return_when=ALL_COMPLETED)


@ray.remote
class EmbeddingControlBasedRayMonitor(EmbeddingControlMonitor):

    def __init__(self, video_config_path: Path, stream_path: Path, sample_path: Path, frame_path: Path,
                 region_path: Path, offline_path: Path = None) -> None:
        super().__init__(video_config_path, stream_path, sample_path, frame_path, region_path, offline_path,
                         build_pool=False)
        self.caps_queue = [ray.put(Manager().Queue(maxsize=500)) for c in self.cfgs]
        self.pipes = [ray.put(Manager().Queue()) for c in self.cfgs]
        self.stream_receivers = [
            stream.StreamRayReceiver.remote(self.stream_path / str(c.index), offline_path, c, self.pipes[idx]) for
            idx, c in
            enumerate(self.cfgs)]
        self.futures = []
        self.caps = self.init_caps()

    def init_caps(self):
        caps = []
        for idx, c in enumerate(self.cfgs):
            if c.online:
                caps.append(
                    VideoOnlineSampleBasedRayCapture.remote(self.stream_path / str(c.index),
                                                            self.sample_path / str(c.index),
                                                            self.pipes[idx],
                                                            self.caps_queue[idx],
                                                            c, c.sample_rate))
            else:
                caps.append(
                    VideoOfflineRayCapture.remote(self.stream_path / str(c.index), self.sample_path / str(c.index),
                                                  self.offline_path,
                                                  self.pipes[idx],
                                                  self.caps_queue[idx], c, self.offline_path / str(c.index),
                                                  c.sample_rate,
                                                  delete_post=False))
        return caps

    def call(self):
        # Init stream receiver firstly, ensures video index that is arrived before detectors begin detection..
        receiver_futures = [self.init_stream_receiver(i) for i, cfg in enumerate(self.cfgs)]
        self.futures.append(receiver_futures)

        # Run video capture from stream
        caps_future = [self.caps[i].read.remote() for i, cfg in enumerate(self.cfgs)]
        self.futures.append(caps_future)

        # Init detector controller
        controller_futures = self.init_controllers()
        self.futures.append(controller_futures)

    def init_controllers(self):
        controller_futures = []
        self.controllers = [
            RayBasedDetectorController.remote(cfg, self.stream_path / str(cfg.index), self.region_path, self.frame_path,
                                              self.caps_queue[idx],
                                              self.pipes[idx]) for
            idx, cfg in enumerate(self.cfgs)]

        for i, cfg in enumerate(self.cfgs):
            logger.info('Init detector controller [{}]....'.format(cfg.index))
            controller_futures.append(self.controllers[i].start.remote(None))
            logger.info('Done init detector controller [{}]....'.format(cfg.index))
        return controller_futures

    def init_stream_receiver(self, i):
        # return self.process_pool.apply_async(self.stream_receivers[i].receive_online)
        return self.stream_receivers[i].receive_online.remote()

    def wait(self):
        super().wait()
        for future in self.futures:
            if isinstance(future, list):
                ray.wait(future)
            else:
                ray.get(future)
        # wait all remote future arrivied in Thread Pool Executor


class DetectorController(object):
    def __init__(self, cfg: VideoConfig, stream_path: Path, region_path: Path, frame_path: Path,
                 frame_queue: Queue,
                 index_pool: Queue) -> None:
        super().__init__()
        self.cfg = cfg
        self.stream_path = stream_path
        self.frame_path = frame_path
        self.region_path = region_path
        self.result_cnt = 0
        # self.process_pool = process_pool
        self.col = cfg.routine['col']
        self.raw = cfg.routine['row']

        self.send_pipes = [Manager().Queue() for i in range(self.col * self.raw)]
        self.receive_pipes = [Manager().Queue() for i in range(self.col * self.raw)]
        self.index_pool = index_pool
        self.frame_queue = frame_queue
        self.result_queue = Manager().Queue()

        self.quit = False

        # def __getstate__(self):

    #     self_dict = self.__dict__.copy()
    #     del self_dict['process_pool']
    #     return self_dict
    #
    # def __setstate__(self, state):
    #     self.__dict__.update(state)

    def init_control_range(self):
        # read a frame, record frame size before running detectors
        frame = self.frame_queue.get()
        frame, original_frame = self.preprocess(frame)
        self.col_step = int(frame.shape[0] / self.col)
        self.row_step = int(frame.shape[1] / self.raw)

    def init_detectors(self):
        logger.info('Init total [{}] detectors....'.format(self.col * self.raw))
        self.detectors = []
        for i in range(self.col):
            for j in range(self.raw):
                region_detector_path = self.region_path / str(self.cfg.index) / (str(i) + '-' + str(j))
                index = self.col * i + j
                logger.info(index)
                self.detectors.append(
                    Detector(self.col_step, self.row_step, i, j, self.cfg, self.send_pipes[index],
                             self.receive_pipes[index],
                             region_detector_path))
        self.result_path = self.region_path / str(self.cfg.index) / 'frames'
        self.result_path.mkdir(exist_ok=True, parents=True)
        logger.info('Detectors init done....')

    def preprocess(self, frame):
        original_frame = frame.copy()
        if self.cfg.resize['scale'] != -1:
            frame = cv2.resize(frame, (0, 0), fx=self.cfg.resize['scale'], fy=self.cfg.resize['scale'])
        elif self.cfg.resize['width'] != -1:
            frame = imutils.resize(frame, width=self.cfg.resize['width'])
        elif self.cfg.resize['height'] != -1:
            frame = imutils.resize(frame, height=self.cfg.resize['height'])
        frame = crop_by_roi(frame, self.cfg.roi)
        # frame = imutils.resize(frame, width=1000)
        # frame = frame[340:, :, :]
        # frame = frame[170:, :, :]
        frame = cv2.GaussianBlur(frame, ksize=(3, 3), sigmaX=0)
        return frame, original_frame

    # def start(self):
    #     self.process_pool.apply_async(self.control, (self,))

    def start(self, pool):
        self.init_control_range()
        self.init_detectors()
        return None

    def write_work(self):
        while True:
            if self.quit:
                break
            r = self.get_result_from_queue()
            self.result_cnt += 1
            current_time = time.strftime('%m-%d-%H:%M-', time.localtime(time.time()))
            target = self.result_path / (current_time + str(self.result_cnt) + '.png')
            # filename = str(self.result_path / (str(self.result_cnt) + '.png'))
            cv2.imwrite(str(target), r)
        return True

    def get_result_from_queue(self):
        return self.result_queue.get()

    def collect_and_reconstruct(self):
        logger.info('Detection controller [{}] start collect and construct'.format(self.cfg.index))
        cnt = 0
        start = time.time()
        while True:
            if self.quit:
                break
            results = self.collect()
            # logger.info('Done collected from detectors.....')
            logger.info('Constructing sub-frames into a original frame....')
            frame, binary, thresh = self.construct(results)
            cnt += 1
            if cnt % 100 == 0:
                end = time.time() - start
                logger.info(
                    'Detection controller [{}]: Operation Speed Rate [{}]s/100fs, unit process rate: [{}]s/f'.format(
                        self.cfg.index, round(end, 2), round(end / 100, 2)))
                start = time.time()
            if self.cfg.draw_boundary:
                frame = self.draw_boundary(frame)
            # logger.info('Done constructing of sub-frames into a original frame....')
            if self.cfg.show_window:
                cv2.imshow('Reconstructed Frame', frame)
                cv2.waitKey(1)
        return True

    def draw_boundary(self, frame):
        shape = frame.shape
        for i in range(self.col - 1):
            start = (0, self.col_step * (i + 1))
            end = (shape[1] - 1, self.col_step * (i + 1))
            cv2.line(frame, start, end, (0, 0, 255), thickness=1)
        for j in range(self.raw - 1):
            start = (self.row_step * (j + 1), 0)
            end = (self.row_step * (j + 1), shape[0] - 1)
            cv2.line(frame, start, end, (0, 0, 255), thickness=1)
        return frame

    def dispatch(self):
        # start = time.time()
        while True:
            if self.quit:
                break
            frame = self.frame_queue.get()
            frame, original_frame = self.preprocess(frame)
            for sp in self.send_pipes:
                # sp.put((frame, original_frame))
                sp.put(frame)
            logger.info('Dispatch frame to all detectors....')
            # internal = (time.time() - start) / 60
            # if int(internal) == self.cfg.sample_internal:
            #     cv2.imwrite(str(self.frame_path / ))
        return True

    def collect(self):
        res = []
        for rp in self.receive_pipes:
            res.append(rp.get())
        logger.info('Collect sub-frames from all detectors....')
        return res

    def construct(self, results: List[DetectionResult]):
        sub_frames = [r.frame for r in results]
        sub_binary = [r.binary for r in results]
        sub_thresh = [r.thresh for r in results]
        constructed_frame = self.construct_rgb(sub_frames)
        constructed_binary = self.construct_gray(sub_binary)
        constructed_thresh = self.construct_gray(sub_thresh)
        for r in results:
            if len(r.regions):
                self.result_queue.put(constructed_frame)
                # self.result_queue.put(r.original_frame)
        return constructed_frame, constructed_binary, constructed_thresh

    def construct_rgb(self, sub_frames):
        sub_frames = np.array(sub_frames)
        sub_frames = np.reshape(sub_frames, (self.col, self.raw, self.col_step, self.row_step, 3))
        sub_frames = np.transpose(sub_frames, (0, 2, 1, 3, 4))
        constructed_frame = np.reshape(sub_frames, (self.col * self.col_step, self.raw * self.row_step, 3))
        return constructed_frame

    def construct_gray(self, sub_frames):
        sub_frames = np.array(sub_frames)
        sub_frames = np.reshape(sub_frames, (self.col, self.raw, self.col_step, self.row_step))
        sub_frames = np.transpose(sub_frames, (0, 2, 1, 3))
        constructed_frame = np.reshape(sub_frames, (self.col * self.col_step, self.raw * self.row_step))
        return constructed_frame


@ray.remote
class RayBasedDetectorController(DetectorController):

    def __init__(self, cfg: VideoConfig, stream_path: Path, region_path: Path, frame_path: Path, frame_queue: Queue,
                 index_pool: Queue) -> None:
        super().__init__(cfg, stream_path, region_path, frame_path, frame_queue, index_pool)
        self.result_path = None
        self.send_pipes = [ray.put(Manager().Queue()) for i in range(self.col * self.raw)]
        self.receive_pipes = [ray.put(Manager().Queue()) for i in range(self.col * self.raw)]
        self.index_pool = ray.get(index_pool)
        self.frame_queue = ray.get(frame_queue)
        self.result_queue = Manager().Queue()
        self.detectors = []
        self.futures = []

    def init_detectors(self):
        logger.info('Init total [{}] detectors....'.format(self.col * self.raw))
        # self.detectors = []
        for i in range(self.col):
            for j in range(self.raw):
                region_detector_path = self.region_path / str(self.cfg.index) / (str(i) + '-' + str(j))
                index = self.col * i + j
                logger.info(index)
                self.detectors.append(
                    RayDetector.remote(self.col_step, self.row_step, i, j, self.cfg, self.send_pipes[index],
                                       self.receive_pipes[index],
                                       region_detector_path))
        self.result_path = self.region_path / str(self.cfg.index) / 'frames'
        self.result_path.mkdir(exist_ok=True, parents=True)
        logger.info('Detectors init done....')

    def start(self, pool):
        # res = pool.apply_async(self.collect_and_reconstruct, ())
        futures = []
        cr_future = self.collect_and_reconstruct.remote()
        futures.append(cr_future)
        # pool.apply_async(self.dispatch, ())
        dispatch_future = self.dispatch_ray_frame.remote()
        futures.append(dispatch_future)
        # thread_res.append(thread_pool.submit(self.write_work))
        write_future = self.write_work.remote()
        futures.append(write_future)
        logger.info('Running detectors.......')
        for idx, d in enumerate(self.detectors):
            logger.info('Submit detector [{},{},{}] task..'.format(self.cfg.index, d.col_index, d.raw_index))
            # detect_proc_res.append(pool.apply_async(d.detect, ()))
            futures.append(d.detect.remote())
            # detect_proc_res.append(pool.submit(d.detect, ()))
            logger.info('Done detector [{},{},{}]'.format(self.cfg.index, d.col_index, d.raw_index))
        return futures

    @ray.method(num_return_vals=2)
    def preprocess(self, frame):
        original_frame = frame.copy()
        if self.cfg.resize['scale'] != -1:
            frame = cv2.resize(frame, (0, 0), fx=self.cfg.resize['scale'], fy=self.cfg.resize['scale'])
        elif self.cfg.resize['width'] != -1:
            frame = imutils.resize(frame, width=self.cfg.resize['width'])
        elif self.cfg.resize['height'] != -1:
            frame = imutils.resize(frame, height=self.cfg.resize['height'])
        frame = crop_by_roi(frame, self.cfg.roi)
        # frame = imutils.resize(frame, width=1000)
        # frame = frame[340:, :, :]
        # frame = frame[170:, :, :]
        frame = cv2.GaussianBlur(frame, ksize=(3, 3), sigmaX=0)
        return frame, original_frame

    @ray.method(num_return_vals=3)
    def construct_ray_frame(self, results):
        res_ready_ids, remaining_ids = ray.wait(results, num_returns=len(results))
        # results = [ray.get(obj_id) for obj_id in res_ready_ids]
        results = ray.get(res_ready_ids)
        sub_frames = [r.frame for r in results]
        sub_binary = [r.binary for r in results]
        sub_thresh = [r.thresh for r in results]
        constructed_frame = self.construct_rgb(sub_frames)
        constructed_binary = self.construct_gray(sub_binary)
        constructed_thresh = self.construct_gray(sub_thresh)
        for r in results:
            if len(r.regions):
                idx = ray.put(constructed_frame)
                self.result_queue.put(idx)
                # self.result_queue.put(r.original_frame)
        return constructed_frame, constructed_binary, constructed_thresh

    def get_result_from_queue(self):
        return ray.get(self.result_queue.get())

    def collect_ray_frame(self):
        res = []
        for rp in self.receive_pipes:
            res.append(rp.get())
        # res_ready_ids, remaining_ids = ray.wait(res, num_returns=len(res))
        # res = [ray.get(obj_id) for obj_id in res_ready_ids]
        logger.info('Collect sub-frames from all detectors....')
        return res

    def dispatch_ray_frame(self, frame):
        # start = time.time()
        while True:
            if self.quit:
                break
            frame = self.frame_queue.get()
            frame_id, original_frame_id = self.preprocess.remote(frame)
            for sp in self.send_pipes:
                # frame_id = ray.put(ray.get(frame))
                sp.put(frame_id)
            logger.info('Dispatch frame to all detectors....')
        # internal = (time.time() - start) / 60
        # if int(internal) == self.cfg.sample_internal:
        #     cv2.imwrite(str(self.frame_path / ))

    def collect_and_reconstruct_ray(self):
        logger.info('Detection controller [{}] start collect and construct'.format(self.cfg.index))
        cnt = 0
        start = time.time()
        while True:
            if self.quit:
                break
            logger.info('Done collected from detectors.....')
            results = self.collect_ray_frame.remote()
            frame_id, binary_id, thresh_id = self.construct_ray_frame.remote(results)
            logger.info('Constructing sub-frames into a original frame....')
            # frame, binary, thresh = self.construct(results)
            cnt += 1
            if cnt % 100 == 0:
                end = time.time() - start
                logger.info(
                    'Detection controller [{}]: Operation Speed Rate [{}]s/100fs, unit process rate: [{}]s/f'.format(
                        self.cfg.index, round(end, 2), round(end / 100, 2)))
                start = time.time()
            if self.cfg.draw_boundary:
                self.draw_boundary.remote(frame_id)
            # logger.info('Done constructing of sub-frames into a original frame....')
        return True

    def show_window(self, frame_id):
        if self.cfg.show_window:
            cv2.imshow('Reconstructed Frame', ray.get(frame_id))
            cv2.waitKey(1)
        return True


class ProcessBasedDetectorController(DetectorController):

    def start(self, pool: Pool):
        super().start(pool)
        res = pool.apply_async(self.collect_and_reconstruct, ())
        pool.apply_async(self.dispatch, ())
        logger.info('Running detectors.......')
        detect_proc_res = []
        for idx, d in enumerate(self.detectors):
            logger.info('Submit detector [{},{},{}] task..'.format(self.cfg.index, d.col_index, d.raw_index))
            detect_proc_res.append(pool.apply_async(d.detect, ()))
            # detect_proc_res.append(pool.submit(d.detect, ()))
            logger.info('Done detector [{},{},{}]'.format(self.cfg.index, d.col_index, d.raw_index))
        return res, detect_proc_res
        # self.monitor.wait_pool()
        # self.loop_work()


class ThreadBasedDetectorController(DetectorController):

    def start(self, pool: ThreadPoolExecutor):
        super().start(pool)
        thread_res = []
        try:
            thread_res.append(pool.submit(self.collect_and_reconstruct))
            thread_res.append(pool.submit(self.dispatch))
            logger.info('Running detectors.......')
            for idx, d in enumerate(self.detectors):
                logger.info(
                    'Submit detector [{},{},{}] task..'.format(self.cfg.index, d.col_index, d.raw_index))
                thread_res.append(pool.submit(d.detect))
                # detect_proc_res.append(pool.submit(d.detect, ()))
                logger.info('Done detector [{},{},{}]'.format(self.cfg.index, d.col_index, d.raw_index))
        except Exception as e:
            traceback.print_exc()
            logger.error(e)
        return thread_res
        # self.monitor.wait_pool()
        # self.loop_work()


class ProcessAndThreadBasedDetectorController(DetectorController):

    def start(self, pool):
        process_pool = pool[0]
        thread_pool = pool[1]
        pool_res = []
        thread_res = []
        super().start(process_pool)
        # collect child frames and reconstruct frames from detectors asynchronously
        pr1 = process_pool.apply_async(self.collect_and_reconstruct, ())
        pool_res.append(pr1)
        # dispatch child frames to detector asynchronously
        pr2 = process_pool.apply_async(self.dispatch, ())
        # write detection result asynchronously
        thread_res.append(thread_pool.submit(self.write_work))
        pool_res.append(pr2)
        logger.info('Running detectors.......')
        for idx, d in enumerate(self.detectors):
            logger.info('Submit detector [{},{},{}] task..'.format(self.cfg.index, d.col_index, d.raw_index))
            thread_res.append(thread_pool.submit(d.detect))
            # detect_proc_res.append(pool.submit(d.detect, ()))
            logger.info('Done detector [{},{},{}]'.format(self.cfg.index, d.col_index, d.raw_index))
        return pool_res, thread_res
        # self.monitor.wait_pool()
        # self.loop_work()
