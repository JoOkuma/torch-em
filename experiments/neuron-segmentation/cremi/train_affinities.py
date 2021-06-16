import numpy as np
import torch_em
from torch_em.model import AnisotropicUNet
from torch_em.loss import DiceLoss, LossWrapper, ApplyAndRemoveMask
from torch_em.data.datasets import get_cremi_loader
from torch_em.util import parser_helper

OFFSETS = [
    [-1, 0, 0], [0, -1, 0], [0, 0, -1],
    [-2, 0, 0], [0, -3, 0], [0, 0, -3],
    [-3, 0, 0], [0, -9, 0], [0, 0, -9],
    [-4, 0, 0], [0, -27, 0], [0, 0, -27]
]


def get_rois(all_samples, is_train):
    val_slice = 75
    if is_train:
        # we train on sampleA + sampleB + 0:75 of sampleC
        if len(all_samples) == 3 or "C" in all_samples:
            rois = {"C": np.s_[:val_slice, :, :]}
        else:  # if we don't have sample C we train on everything
            rois = {}
        samples = all_samples
    else:
        # we validate on 75:125 of sampleC, regardless of inut samples
        rois = {"C": np.s_[val_slice:, :, :]}
        samples = ["C"]
    return samples, rois


def get_loader(input_path, all_samples, is_train, patch_shape, batch_size=1, n_samples=None):
    samples, rois = get_rois(all_samples, is_train)
    return get_cremi_loader(
        path=input_path,
        samples=samples,
        patch_shape=patch_shape,
        batch_size=batch_size,
        rois=rois,
        offsets=OFFSETS,
        n_samples=n_samples,
        num_workers=8*batch_size,
        shuffle=True,
        download=True
    )


def get_model(large_model):
    n_out = len(OFFSETS)
    if large_model:
        print("Using large model")
        model = AnisotropicUNet(
            scale_factors=[
                [1, 3, 3],
                [1, 3, 3],
                [2, 2, 2],
                [2, 2, 2],
                [2, 2, 2]
            ],
            in_channels=1,
            out_channels=n_out,
            initial_features=128,
            gain=2,
            final_activation='Sigmoid'
        )
    else:
        print("Using vanilla model")
        model = AnisotropicUNet(
            scale_factors=[
                [1, 3, 3],
                [1, 3, 3],
                [2, 2, 2],
                [2, 2, 2],
                [2, 2, 2]
            ],
            in_channels=1,
            out_channels=n_out,
            initial_features=32,
            gain=2,
            final_activation='Sigmoid'
        )
    return model


def normalize_samples(samples):
    if set(samples) == {'A', 'B', 'C'}:
        samples = ('A', 'B', 'C')
        prefix = None
    elif set(samples) == {'A', 'B'}:
        samples = ('A', 'B')
        prefix = 'AB'
    elif set(samples) == {'A', 'C'}:
        samples = ('A', 'C')
        prefix = 'AC'
    elif set(samples) == {'B', 'C'}:
        samples = ('B', 'C')
        prefix = 'BC'
    return samples, prefix


def train_affinities(args):
    large_model = bool(args.large_model)
    model = get_model(large_model)
    # patch shapes:
    if large_model:
        # TODO determine patch shape
        # largest possible shape for A100 with mixed training and large model
        # patch_shape = [32, 320, 320]
        patch_shape = [32, 256, 256]
    else:
        patch_shape = [32, 360, 360]

    samples, prefix = normalize_samples(args.samples)
    train_loader = get_loader(
        args.input, samples,
        is_train=True,
        patch_shape=patch_shape,
        n_samples=1000
    )
    val_loader = get_loader(
        args.input, samples,
        is_train=False,
        patch_shape=patch_shape,
        n_samples=100
    )

    loss = LossWrapper(loss=DiceLoss(),
                       transform=ApplyAndRemoveMask())

    tag = 'large' if large_model else 'default'
    if prefix is not None:
        tag += f"_{prefix}"
    name = f"affinity_model_{tag}"
    trainer = torch_em.default_segmentation_trainer(
        name=name,
        model=model,
        train_loader=train_loader,
        val_loader=val_loader,
        loss=loss,
        metric=loss,
        learning_rate=1e-4,
        mixed_precision=True,
        log_image_interval=50
    )

    if args.from_checkpoint:
        trainer.fit(args.n_iterations, 'latest')
    else:
        trainer.fit(args.n_iterations)


def check(args, train=True, val=True, n_images=2):
    from torch_em.util.debug import check_loader
    patch_shape = [32, 256, 256]
    samples, _ = normalize_samples(args.samples)
    if train:
        print("Check train loader")
        loader = get_loader(args.input, samples, True, patch_shape)
        check_loader(loader, n_images)
    if val:
        print("Check val loader")
        loader = get_loader(args.input, samples, False, patch_shape)
        check_loader(loader, n_images)


# TODO advanced cremi training (probably should make 'train_advanced.py' for this)
# - more defect and mis-alignment augmentations
# -- pasting defect patches
# -- simulating contrast defect
# -- simulating tear defect
# -- alignment jitter
# - more augmentations
# -- elastic
# - training with aligned volumes (needs ignore mask)
# - training with glia mask
if __name__ == '__main__':
    parser = parser_helper()
    parser.add_argument('--large_model', '-l', type=int, default=0)
    parser.add_argument('--samples', type=str, nargs='+', default=['A', 'B', 'C'])

    args = parser.parse_args()
    if args.check:
        check(args, train=True, val=True)
    else:
        train_affinities(args)
