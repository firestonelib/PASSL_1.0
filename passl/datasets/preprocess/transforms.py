# Copyright (c) 2021 PaddlePaddle Authors. All Rights Reserve.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import random
from PIL import ImageFilter, Image, ImageOps
import numpy as np

import paddle
import paddle.vision.transforms as PT
import paddle.vision.transforms.functional as F

from .mixup import Mixup
from .builder import TRANSFORMS, build_transform
from .random_erasing import RandomErasing
from .constants import IMAGENET_DEFAULT_MEAN, IMAGENET_DEFAULT_STD, DEFAULT_CROP_PCT
from .auto_augment import rand_augment_transform, augment_and_mix_transform, auto_augment_transform
from .cv2_trans import ByolRandomHorizontalFlip, ByolColorJitter, ByolRandomGrayscale, ByolNormalize, \
         ToCHW, ByolToRGB, ByolCenterCrop, ByolRandomCrop

TRANSFORMS.register(PT.RandomResizedCrop)
TRANSFORMS.register(PT.ColorJitter)
TRANSFORMS.register(PT.Transpose)
TRANSFORMS.register(PT.Normalize)
TRANSFORMS.register(PT.RandomHorizontalFlip)
TRANSFORMS.register(PT.Resize)
TRANSFORMS.register(PT.CenterCrop)
TRANSFORMS.register(PT.ToTensor)

TRANSFORMS.register(ByolRandomHorizontalFlip)
TRANSFORMS.register(ByolColorJitter)
TRANSFORMS.register(ByolRandomGrayscale)
TRANSFORMS.register(ByolNormalize)
TRANSFORMS.register(ToCHW)
TRANSFORMS.register(ByolToRGB)
TRANSFORMS.register(ByolRandomCrop)
TRANSFORMS.register(ByolCenterCrop)

TRANSFORMS.register(RandomErasing)
TRANSFORMS.register(Mixup)


@TRANSFORMS.register()
class Clip():
    def __init__(self, min_val=0.0, max_val=1.0):
        self.min_val = min_val
        self.max_val = max_val

    def __call__(self, img):
        """
        Args:
            img (PIL Image): Image to be converted to grayscale.

        Returns:
            PIL Image: Cliped image.
        """
        clip_img = img.clip(self.min_val, self.max_val)
        return clip_img


@TRANSFORMS.register()
class NormToOne():
    def __init__(self):
        pass

    def __call__(self, img):
        """
        Args:
            img (PIL Image): Image to be converted to grayscale.

        Returns:
            PIL Image: Randomly grayscaled image.
        """
        norm_img = (img / 255.).astype('float32')
        return norm_img


@TRANSFORMS.register()
class RandomApply():
    """Apply randomly a list of transformations with a given probability

    Args:
        transforms (list or tuple): list of transformations
        p (float): probability
    """
    def __init__(self, transforms, p=0.5):
        _transforms = []
        if isinstance(transforms, (list, tuple)):
            for transform in transforms:
                if isinstance(transform, dict):
                    _transforms.append(build_transform(transform))
                else:
                    _transforms.append(transform)

        self.transforms = _transforms
        self.p = p

    def __call__(self, img):
        if self.p < random.random():
            return img
        for t in self.transforms:
            img = t(img)
        return img


@TRANSFORMS.register()
class RandomGrayscale(object):
    """Randomly convert image to grayscale with a probability of p (default 0.1).

    Args:
        p (float): probability that image should be converted to grayscale.

    Returns:
        PIL Image: Grayscale version of the input image with probability p and unchanged
        with probability (1-p).
        - If input image is 1 channel: grayscale version is 1 channel
        - If input image is 3 channel: grayscale version is 3 channel with r == g == b

    """
    def __init__(self, p=0.1):
        self.p = p

    def __call__(self, img):
        """
        Args:
            img (PIL Image): Image to be converted to grayscale.

        Returns:
            PIL Image: Randomly grayscaled image.
        """
        num_output_channels = 1 if img.mode == 'L' else 3

        if random.random() < self.p:
            return F.to_grayscale(img, num_output_channels=num_output_channels)
        return img


@TRANSFORMS.register()
class GaussianBlur(object):
    """Gaussian blur augmentation in SimCLR https://arxiv.org/abs/2002.05709"""
    def __init__(self, sigma=[.1, 2.], _PIL=False):
        self.sigma = sigma
        self.kernel_size = 23
        self._PIL = _PIL

    def __call__(self, x):
        sigma = np.random.uniform(self.sigma[0], self.sigma[1])
        if self._PIL:
            x = x.filter(ImageFilter.GaussianBlur(radius=sigma))
            return x
        else:
            import cv2
            x = cv2.GaussianBlur(np.array(x),
                                 (self.kernel_size, self.kernel_size), sigma)
            return Image.fromarray(x.astype(np.uint8))


@TRANSFORMS.register()
class Solarization(object):
    def __init__(self, threshold=128):
        self.threshold = threshold

    def __call__(self, sample):
        return ImageOps.solarize(sample, self.threshold)


@TRANSFORMS.register()
class ToRGB(object):
    def __init__(self, mode='RGB'):
        self.mode = mode

    def __call__(self, sample):
        if sample.mode != self.mode:
            return sample.convert(self.mode)
        return sample


def _pil_interp(method):
    if method == 'bicubic':
        return Image.BICUBIC
    elif method == 'lanczos':
        return Image.LANCZOS
    elif method == 'hamming':
        return Image.HAMMING
    else:
        # default bilinear, do we want to allow nearest?
        return Image.BILINEAR


@TRANSFORMS.register()
class AutoAugment(PT.BaseTransform):
    def __init__(self,
                 config_str,
                 img_size,
                 interpolation,
                 mean=IMAGENET_DEFAULT_MEAN,
                 std=IMAGENET_DEFAULT_STD,
                 keys=None):
        super(AutoAugment, self).__init__(keys)
        assert isinstance(config_str, str)
        if isinstance(img_size, tuple):
            img_size_min = min(img_size)
        else:
            img_size_min = img_size
        aa_params = dict(
            translate_const=int(img_size_min * 0.45),
            img_mean=tuple([min(255, round(255 * x)) for x in mean]),
        )
        if interpolation and interpolation != 'random':
            aa_params['interpolation'] = _pil_interp(interpolation)
        if config_str.startswith('rand'):
            self.transform = rand_augment_transform(config_str, aa_params)
        elif config_str.startswith('augmix'):
            aa_params['translate_pct'] = 0.3
            self.transform = augment_and_mix_transform(config_str, aa_params)
        elif config_str == '':
            self.transform = None
        else:
            self.transform = auto_augment_transform(config_str, aa_params)

    def _apply_image(self, img):
        return self.transform(img) if self.transform != None else img
