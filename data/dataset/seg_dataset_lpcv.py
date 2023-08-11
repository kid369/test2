import json
import numpy as np
import os
from easydict import EasyDict

from up.data.datasets.base_dataset import BaseDataset
from up.utils.general.registry_factory import DATASET_REGISTRY
from up.data.image_reader import build_image_reader
from up.utils.general.registry import Registry
from up.utils.general.petrel_helper import PetrelHelper
from up.utils.general.log_helper import default_logger as logger
from up.utils.env.dist_helper import env
from up.tasks.seg.data.seg_evaluator import intersectionAndUnion

__all__ = ['SegLPCVDataset']


SEG_PARSER_REGISTRY = Registry()


class BaseParser(object):
    def __init__(self, extra_info={}):
        self.extra_info = extra_info

    def parse(self):
        raise NotImplementedError


@SEG_PARSER_REGISTRY.register('cityscapes')
class CityScapesParser(BaseParser):
    def parse(self, meta_file, idx, metas):
        with PetrelHelper.open(meta_file) as fin:
            for line in fin:
                cls_res = {}
                spts = line.strip().split()
                cls_res['filename'] = spts[0]
                cls_res['seg_label_filename'] = spts[1]
                cls_res['image_source'] = idx
                metas.append(cls_res)
        return metas

@DATASET_REGISTRY.register('seg_lpcv')
class SegLPCVDataset(BaseDataset):
    def __init__(self,
                 meta_file,
                 image_reader,
                 transformer=None,
                 evaluator=None,
                 seg_type='cityscapes',
                 parser_info={},
                 seg_label_reader=None,
                 output_pred=False,
                 output_gt=False,
                 ignore_label=255,
                 num_classes=19):
        super(SegLPCVDataset, self).__init__(meta_file,
                                         image_reader,
                                         transformer,
                                         evaluator)
        self.seg_type = seg_type
        assert seg_label_reader is not None
        self.seg_label_reader = build_image_reader(seg_label_reader)
        self._list_check()
        self.meta_parser = [SEG_PARSER_REGISTRY[m_type](**parser_info) for m_type in self.seg_type]
        self.parse_metas()
        self.output_pred = output_pred
        self.output_gt = output_gt
        self.ignore_label = ignore_label
        self.num_classes = num_classes

    def parse_metas(self):
        self.metas = []
        for idx, meta_file in enumerate(self.meta_file):
            self.meta_parser[idx].parse(meta_file, idx, self.metas)

    def _list_check(self):
        if not isinstance(self.meta_file, list):
            self.meta_file = [self.meta_file]
        if not isinstance(self.seg_type, list):
            self.seg_type = [self.seg_type]

    def __len__(self):
        return len(self.metas)

    def _load_meta(self, idx):
        return self.metas[idx]

    def get_input(self, idx):
        meta = self._load_meta(idx)
        img = self.image_reader(meta['filename'], meta.get('image_source', 0))
        seg_label = self.seg_label_reader(meta['seg_label_filename'], meta.get('image_source', 0))
        input = EasyDict({
            'image': img,
            'gt_semantic_seg': seg_label
        })
        return input

    def __getitem__(self, idx):
        input = self.get_input(idx)
        if self.transformer is not None:
            input = self.transformer(input)
        input.image_info = input['image'].size()
        return input

    def dump(self, output):
        pred = output['blob_pred'].max(1)[1]
        pred = self.tensor2numpy(pred)
        if 'gt_semantic_seg' in output and output['gt_semantic_seg'] is not None:
            seg_label = self.tensor2numpy(output['gt_semantic_seg'])
        else:
            seg_label = np.zeros((pred.shape))
        out_res = []
        for _idx in range(pred.shape[0]):
            if 'gt_semantic_seg' in output and output['gt_semantic_seg'] is not None:
                inter, union, target = intersectionAndUnion(pred[_idx],
                                                            seg_label[_idx],
                                                            self.num_classes,
                                                            self.ignore_label)
                res = {
                    'inter': inter.tolist(),
                    'union': union.tolist(),
                    'target': target.tolist()
                }
            else:
                res = {}
            if self.output_pred:
                res['pred'] = pred[_idx]
            if self.output_gt:
                res['gt'] = seg_label[_idx]
            out_res.append(res)
        return out_res
