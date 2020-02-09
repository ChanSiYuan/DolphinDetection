#!/usr/bin/env python
# encoding: utf-8
"""
@author: Shanda Lau 刘祥德
@license: (C) Copyright 2019-now, Node Supply Chain Manager Corporation Limited.
@contact: shandalaulv@gmail.com
@software: 
@file: capture.py
@time: 2019/11/24 11:25
@version 1.0
@desc:
"""
import os
import shutil
import threading
import time
from multiprocessing import Manager
from multiprocessing.queues import Queue
from pathlib import Path

import cv2

from config import VideoConfig, SystemStatus
from utils import logger


# from .manager import TaskBasedDetectorController


# import ray


class VideoCaptureThreading:
    def __init__(self, video_path: Path, sample_path: Path, index_pool: Queue, frame_queue: Queue, cfg: VideoConfig,
                 idx,
                 sample_rate=5, width=640, height=480, delete_post=True):
        self.cfg = cfg
        self.video_path = video_path
        self.sample_path = sample_path
        self.index_pool = index_pool
        self.idx = idx
        # self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        # self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        # self.grabbed, self.frame = self.cap.read()
        self.status = Manager().Value('i', SystemStatus.SHUT_DOWN)
        self.src = -1
        self.cap = None
        self.sample_rate = sample_rate
        self.frame_queue = frame_queue
        self.delete_post = delete_post
        self.runtime = 0
        self.posix = None
        self.quit = Manager().Event()
        self.quit.clear()

    def set(self, var1, var2):
        self.cap.set(var1, var2)

    def __start__(self, *args):
        if self.status.get() == SystemStatus.RUNNING:
            print('[!] Threaded video capturing has already been started.')
            return None
        self.update_capture(0)
        # src = self.load_next_src()
        # logger.info('Loading next video stream from [{}]....'.format(src))
        # self.cap = cv2.VideoCapture(src)
        # logger.info('Loading done from: [{}]'.format(src))
        self.status.set(SystemStatus.RUNNING)
        threading.Thread(target=self.update, args=(*args,), daemon=True).start()
        threading.Thread(target=self.listen, args=(), daemon=True).start()
        # threading.Thread(target=cpu_usage).start()
        return self

    # def cal_cpu_usage(self):
    #         cpu_usage(id)

    def listen(self):
        logger.info('Video Capture [{}]: Start listen event'.format(self.cfg.index))
        if self.quit.wait():
            logger.info('Video Capture [{}]: Receive quit signal'.format(self.cfg.index))
            self.cancel()

    def cancel(self):
        self.status.set(SystemStatus.SHUT_DOWN)

    def load_next_src(self):
        logger.debug('Loading video stream from video index pool....')
        self.posix = self.get_posix()
        self.src = str(self.posix)
        if self.posix == -1:
            return self.src
        basename = os.path.basename(self.src)
        filename, extention = os.path.splitext(basename)
        if extention == '.mp4' or extention == '.mov':
            return self.src
        else:
            return 0

    def get_posix(self):
        return self.video_path / self.index_pool.get()

    def update(self, *args):
        cnt = 0
        start = time.time()
        logger.info('*******************************Init video capture [{}]********************************'.format(
            self.cfg.index))
        while self.status.get() == SystemStatus.RUNNING:
            # with self.read_lock:
            grabbed, frame = self.cap.read()
            if not grabbed:
                self.update_capture(cnt)
                end = time.time()
                logger.info('Current src consumes time: [{}] seconds'.format(end - start))
                start = time.time()
                cnt = 0
                continue
            # if cnt % self.sample_rate == 0:
            self.pass_frame(frame, args[0])
            cnt += 1
            self.post_frame_process(frame)
            self.runtime = time.time() - start

        logger.info(
            '*******************************Video capture [{}] exit********************************'.format(
                self.cfg.index))
        # logger.info('Video Capture [{}]: cancel..'.format(self.cfg.index))

    def pass_frame(self, *args):
        self.frame_queue.put(args[0], block=True)
        # logger.info('Passed frame...')

    def update_capture(self, cnt):
        logger.debug('Read frame done from [{}].Has loaded [{}] frames'.format(self.src, cnt))
        logger.debug('Read next frame from video ....')
        self.handle_history()
        while True:
            src = self.load_next_src()
            if src == str(-1):
                self.cancel()
                return False
            elif src == 0:
                continue
            else:
                break
        if self.cap is not None:
            self.cap.release()
        self.cap = cv2.VideoCapture(src)
        return True

    def handle_history(self):
        if self.posix.exists() and self.delete_post:
            self.posix.unlink()

    def post_update(self):
        pass

    def post_frame_process(self, frame):
        pass

    def read(self, *args):
        if self.status.get() == SystemStatus.SHUT_DOWN:
            self.__start__(*args)
        return True
        # with self.read_lock:
        # frame = self.frame.copy()
        # grabbed = self.grabbed
        # return self.grabbed, self.frame

    def stop(self):
        self.status.set(SystemStatus.SHUT_DOWN)
        self.cap.release()
        self.thread.join()

    def __exit__(self, exec_type, exc_value, traceback):
        self.cap.release()


class VideoOfflineCapture(VideoCaptureThreading):
    def __init__(self, video_path: Path, sample_path: Path, offline_path: Path, index_pool: Queue, frame_queue: Queue,
                 cfg: VideoConfig, idx, sample_rate=5, width=640, height=480, delete_post=True):
        super().__init__(video_path, sample_path, index_pool, frame_queue, cfg, idx, sample_rate, width, height,
                         delete_post)
        self.offline_path = offline_path
        self.streams_list = list(self.offline_path.glob('*'))
        self.pos = -1

    def get_posix(self):
        self.pos += 1
        if self.pos >= len(self.streams_list):
            logger.info('Load completely for [{}]'.format(str(self.offline_path)))
            return -1
        return self.streams_list[self.pos]

    # def load_next_src(self):
    #     logger.debug('Loading next video stream ....')
    #     if self.pos >= len(self.streams_list):
    #         logger.info('Load completely for [{}]'.format(str(self.offline_path)))
    #         return -1
    #     self.posix = self.streams_list[self.pos]
    #     self.src = str(self.posix)
    #     self.pos += 1
    #     if not os.path.exists(self.src):
    #         logger.debug('Video path not exist: [{}]'.format(self.src))
    #         return -1
    #     return self.src

    def handle_history(self):
        if self.delete_post:
            self.posix.unlink()


class VideoOfflineCallbackCapture(VideoOfflineCapture):

    def __init__(self, video_path: Path, sample_path: Path, offline_path: Path, index_pool: Queue, frame_queue: Queue,
                 cfg: VideoConfig, idx, controller, shut_down_event, sample_rate=5, width=640, height=480,
                 delete_post=True):
        super().__init__(video_path, sample_path, offline_path, index_pool, frame_queue, cfg, idx, sample_rate, width,
                         height, delete_post)
        self.controller = controller
        self.shut_down_event = shut_down_event

    def pass_frame(self, *args):
        assert len(args) >= 2
        self.controller.dispatch_frame(*args)

    def cancel(self):
        super().cancel()
        if not self.shut_down_event.is_set():
            self.shut_down_event.set()


# Sample video stream at intervals
class VideoOnlineSampleCapture(VideoCaptureThreading):
    def __init__(self, video_path: Path, sample_path: Path, index_pool: Queue, frame_queue: Queue, cfg: VideoConfig,
                 idx,
                 sample_rate=5, width=640, height=480, delete_post=True):
        super().__init__(video_path, sample_path, index_pool, frame_queue, cfg, idx, sample_rate, width, height,
                         delete_post)
        self.sample_cnt = 0
        self.sample_path.mkdir(exist_ok=True, parents=True)

    def handle_history(self):
        if int(self.runtime / 60 + 1) % self.cfg.sample_internal == 0:
            current_time = time.strftime('%m-%d-%H:%M-', time.localtime(time.time()))
            filename = os.path.basename(str(self.posix))
            target = self.sample_path / (current_time + filename)
            logger.info('Sample video stream into: [{}]'.format(target))
            shutil.copy(self.posix, target)
        super().handle_history()


# @ray.remote
class VideoOfflineRayCapture(VideoCaptureThreading):
    def __init__(self, video_path: Path, sample_path: Path, offline_path: Path, index_pool: Queue, frame_queue: Queue,
                 cfg: VideoConfig, idx, sample_rate=5, width=640, height=480, delete_post=True):
        super().__init__(video_path, sample_path, index_pool, frame_queue, cfg, idx, sample_rate, width, height,
                         delete_post)
        self.offline_path = offline_path
        self.streams_list = list(self.offline_path.glob('*'))
        self.pos = 0

    def get_posix(self):
        if self.pos >= len(self.streams_list):
            logger.info('Load completely for [{}]'.format(str(self.offline_path)))
            return -1
        return self.streams_list[self.pos]


# @ray.remote(num_cpus=0.5)
class VideoOnlineSampleBasedRayCapture(VideoCaptureThreading):
    def __init__(self, video_path: Path, sample_path: Path, index_pool: Queue, frame_queue: Queue, cfg: VideoConfig,
                 idx,
                 controller_actor,
                 sample_rate=5, width=640, height=480, delete_post=True):
        super().__init__(video_path, sample_path, index_pool, frame_queue, cfg, idx, sample_rate, width, height,
                         delete_post)
        # if ray_index_pool is None:
        #     raise Exception('Invalid index pool object id.')
        # self.ray_index_pool = ray.get(ray_index_pool)
        self.controller_actor = controller_actor
        self.current = 0
        self.stream_futures = []
        # self.frame_queue = ray.get(frame_queue)

    def pass_frame(self, frame):
        # put the ray id of frame into global shared memory
        # frame_id = ray.put(frame)
        # self.frame_queue.put(frame_id)
        self.stream_futures.append(self.controller_actor.start_stream_task.remote(frame))
        logger.info('Passing frame [{}]'.format(self.current))
        self.current += 1
        # if self.current > 100:
        #     logger.info('Blocked cap wait stream complete.')
        #     ray.wait(self.stream_futures)
        #     logger.info('Release cap.')
        #     self.current = 0

    def remote_update(self, src):
        cnt = 0
        start = time.time()
        self.set_posix(src)
        self.cap = cv2.VideoCapture(str(self.posix))
        while True:
            # with self.read_lock:
            grabbed, frame = self.cap.read()
            # logger.info('Video Capture [{}]: cnt ..'.format(cnt))
            if not grabbed:
                # self.update_capture(cnt)
                break
            if (cnt + 1) % 20 == 0:
                time.sleep(1)
            if cnt % self.sample_rate == 0:
                self.pass_frame(frame)
            cnt += 1
            self.runtime = time.time() - start
        self.handle_history()
        self.cap.release()
        # logger.info('Video Capture [{}]: cancel..'.format(self.cfg.index))
        return self.posix

    def set_posix(self, src):
        self.posix = self.video_path / src


# Read stream from rtsp
class VideoRtspCapture(VideoOnlineSampleCapture):
    def __init__(self, video_path: Path, sample_path: Path, index_pool: Queue, frame_queue: Queue, cfg: VideoConfig,
                 idx,
                 sample_rate=5, width=640, height=480, delete_post=True):
        super().__init__(video_path, sample_path, index_pool, frame_queue, cfg, idx, sample_rate, width, height,
                         delete_post)
        self.sample_path.mkdir(exist_ok=True, parents=True)
        self.saved_time = ""
        self.sample_cnt = 0
        self.frame_cnt = 0

    def load_next_src(self):
        logger.debug("Loading next video rtsp stream ....")
        return self.cfg.rtsp

    def handle_history(self):
        pass

    def update(self, *args):
        cnt = 0
        start = time.time()
        logger.info('*******************************Init video capture [{}]********************************'.format(
            self.cfg.index))
        while self.status.get() == SystemStatus.RUNNING:
            # with self.read_lock:
            s = time.time()
            grabbed, frame = self.cap.read()
            e = 1 / (time.time() - s)
            # logger.info(self.cap.get(cv2.CAP_PROP_POS_MSEC))
            # logger.info(self.cap.getRTPTimeStampTs())
            logger.info(
                'Video capture [{}]: Receive Rate [{}]/FPS'.format(
                    self.cfg.index, round(e, 2)))
            s = time.time()
            if not grabbed:
                self.update_capture(cnt)
                cnt = 0
                continue
            # if cnt % self.sample_rate == 0:
            self.pass_frame(frame, args[0])
            e = 1 / (time.time() - s)
            logger.info(
                'Video capture [{}]: Operation Speed Rate [{}]/FPS'.format(
                    self.cfg.index, round(e, 2)))
            self.post_frame_process(frame)
            cnt += 1
            self.runtime = time.time() - start
            # if self.quit:
            #     self.cancel()
        # logger.info('Video Capture [{}]: cancel..'.format(self.cfg.index))
        logger.info(
            '*******************************Video capture [{}] exit********************************'.format(
                self.cfg.index))

    def post_frame_process(self, frame):
        self.sample_cnt += 1
        if self.sample_cnt % self.cfg.rtsp_saved_per_frame and self.cfg.enable_sample_frame:
            current_time = time.strftime('%m-%d-%H-%M-', time.localtime(time.time()))
            self.sample_cnt = 0
            # if current_time != self.saved_time:
            #     self.sample_cnt = 0
            # self.saved_time = current_time
            self.frame_cnt += 1
            target = self.sample_path / (current_time + str(self.frame_cnt) + '.png')
            logger.info("Sample rtsp video stream into: [{}]".format(target))
            cv2.imwrite(str(target), frame)


class VideoRtspCallbackCapture(VideoRtspCapture):
    def __init__(self, video_path: Path, sample_path: Path, index_pool: Queue, frame_queue: Queue, cfg: VideoConfig,
                 idx,
                 controller,
                 sample_rate=5, width=640, height=480, delete_post=True):
        super().__init__(video_path, sample_path, index_pool, frame_queue, cfg, idx, sample_rate, width, height,
                         delete_post)
        self.controller = controller

    def pass_frame(self, *args):
        assert len(args) >= 2
        self.controller.dispatch_frame(args[0], args[1])
