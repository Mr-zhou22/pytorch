import sys

import torch
import torch.distributed as dist
import torch.nn as nn
from torch.distributed.fsdp import FullyShardedDataParallel as FSDP
from torch.testing._internal.common_fsdp import (
    FSDPTest,
    NestedWrappedModule,
)
from torch.testing._internal.common_utils import (
    TEST_WITH_DEV_DBG_ASAN,
    run_tests,
)

if not dist.is_available():
    print("Distributed not available, skipping tests", file=sys.stderr)
    sys.exit(0)

if TEST_WITH_DEV_DBG_ASAN:
    print(
        "Skip dev-asan as torch + multiprocessing spawn have known issues",
        file=sys.stderr,
    )
    sys.exit(0)


class TestApply(FSDPTest):
    @property
    def world_size(self):
        return 2

    @torch.no_grad()
    def _init_linear_weights(self, m):
        if type(m) == nn.Linear:
            m.weight.fill_(1.0)
            m.bias.fill_(1.0)

    @property
    def process_group(self):
        return dist.distributed_c10d._get_default_group()

    def check_weights(self, fsdp, expected_tensor_fn, check):
        with fsdp._summon_full_params(recurse=True):
            linear_modules = [
                module for module in fsdp.modules() if type(module) == nn.Linear
            ]
            for module in linear_modules:
                for param in module.parameters():
                    expected = expected_tensor_fn(param)
                    check(param, expected)

    def _check_apply(self, fsdp):
        # Assert linear weights are not all 1.0
        self.check_weights(
            fsdp, lambda param: torch.ones_like(param), self.assertNotEqual
        )

        fsdp.apply(self._init_linear_weights)

        # Ensure all weights are 1.0
        self.check_weights(fsdp, lambda param: torch.ones_like(param), self.assertEqual)

    def test_nested_module_apply(self):
        """
        Checks apply() modifies weights appropriately on a nested FSDP instance.
        """
        nested_module = NestedWrappedModule(
            self.process_group, wrap_fsdp=True, wrap_everything=True
        )
        fsdp_module = FSDP(nested_module, self.process_group).cuda(self.rank)
        self._check_apply(fsdp_module)

    def test_transformer_module_apply(self):
        """
        Checks apply() modifiees weights appropriately on a wrapped Transformer
        module.
        """
        transformer = self._get_wrapped_model(group=self.process_group).cuda(self.rank)
        # Assert linear weights are not all 1.0
        self.check_weights(
            transformer, lambda param: torch.ones_like(param), self.assertNotEqual
        )
        transformer.apply(self._init_linear_weights)
        # Assert all weights are 1.0
        self.check_weights(
            transformer, lambda param: torch.ones_like(param), self.assertEqual
        )

    def test_apply_in_summon_raises_error(self):
        """
        Ensures that if user calls apply() on FSDP instance within full param
        summon context, appropriate error is raised.
        """
        transformer = self._get_wrapped_model(group=self.process_group).cuda(self.rank)
        with transformer._summon_full_params(recurse=True):
            with self.assertRaisesRegex(ValueError, "expected to be in states"):
                transformer.apply(self._init_linear_weights)


if __name__ == "__main__":
    run_tests()
