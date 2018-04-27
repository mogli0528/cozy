import unittest

from cozy.target_syntax import *
from cozy.syntax_tools import pprint, alpha_equivalent, free_vars
from cozy.typecheck import retypecheck
from cozy.contexts import RootCtx
from cozy.pools import RUNTIME_POOL
from cozy.synthesis.acceleration import map_accelerate

class TestAccelerationRules(unittest.TestCase):

    def test_map_accel_regression1(self):
        xs = EVar("xs").with_type(INT_BAG)
        x = EVar("x").with_type(INT)
        y = EVar("y").with_type(INT)
        ctx = RootCtx(state_vars=[xs], args=[])
        e = EFilter(EStateVar(xs), ELambda(x, EEq(x, ONE)))
        assert retypecheck(e)
        assert ctx.legal_for(free_vars(e))
        results = []
        for ee, pool in map_accelerate(e, ctx):
            assert ctx.legal_for(free_vars(ee)), pprint(ee)
            if pool == RUNTIME_POOL:
                results.append(ee)

        expected = EMapGet(EStateVar(EMakeMap2(xs, ELambda(y, EFilter(xs, ELambda(x, EEq(x, y)))))), ONE)
        assert any(alpha_equivalent(ee, expected) for ee in results), "results = {}".format("; ".join(pprint(ee) for ee in results))
