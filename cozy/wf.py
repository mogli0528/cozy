import itertools

from cozy.common import typechecked
from cozy.typecheck import is_collection, is_scalar, is_numeric
from cozy.target_syntax import *
from cozy.syntax_tools import enumerate_fragments_and_pools, pprint
from cozy.solver import valid
from cozy.pools import RUNTIME_POOL, STATE_POOL

class ExpIsNotWf(Exception):
    def __init__(self, e, offending_subexpression, reason):
        super().__init__(reason)
        self.e = e
        self.offending_subexpression = offending_subexpression
        self.reason = reason

@typechecked
def exp_wf_nonrecursive(e : Exp, state_vars : {EVar}, args : {EVar}, pool = RUNTIME_POOL, assumptions : Exp = T):
    at_runtime = pool == RUNTIME_POOL
    if isinstance(e, EStateVar) and not at_runtime:
        raise ExpIsNotWf(e, e, "EStateVar in state pool position")
    if isinstance(e, EWithAlteredValue) and not at_runtime:
        raise ExpIsNotWf(e, e, "EWithAlteredValue in state position")
    if (isinstance(e, EDropFront) or isinstance(e, EDropBack)) and not at_runtime:
        raise ExpIsNotWf(e, e, "EDrop* in state position")
    if isinstance(e, EFlatMap) and not at_runtime:
        raise ExpIsNotWf(e, e, "EFlatMap in state position")
    if not at_runtime and isinstance(e, EBinOp) and is_numeric(e.type):
        raise ExpIsNotWf(e, e, "arithmetic in state position")
    # if isinstance(e, EUnaryOp) and e.op == UOp.Distinct and not at_runtime:
    #     raise ExpIsNotWf(e, e, "'distinct' in state position")
    # if isinstance(e, EMapKeys) and not at_runtime:
    #     raise ExpIsNotWf(e, e, "'mapkeys' in state position")
    if isinstance(e, EVar):
        if at_runtime and e in state_vars:
            raise ExpIsNotWf(e, e, "state var at runtime")
        elif not at_runtime and e in args:
            raise ExpIsNotWf(e, e, "arg in state exp")
    if is_collection(e.type) and is_collection(e.type.t):
        raise ExpIsNotWf(e, e, "collection of collection")
    if isinstance(e.type, TMap) and not is_scalar(e.type.k):
        raise ExpIsNotWf(e, e, "bad key type {}".format(pprint(e.type.k)))
    if isinstance(e.type, TMap) and isinstance(e.type.v, TMap):
        raise ExpIsNotWf(e, e, "map to map")
    if isinstance(e, EUnaryOp) and e.op == UOp.The:
        len = EUnaryOp(UOp.Length, e.e).with_type(INT)
        if not valid(EImplies(assumptions, EBinOp(len, "<=", ENum(1).with_type(INT)).with_type(BOOL))):
            raise ExpIsNotWf(e, e, "illegal application of 'the': could have >1 elems")
    if not at_runtime and isinstance(e, EBinOp) and e.op == "-" and is_collection(e.type):
        raise ExpIsNotWf(e, e, "collection subtraction in state position")
    if not at_runtime and isinstance(e, ESingleton):
        raise ExpIsNotWf(e, e, "singleton in state position")
    if not at_runtime and isinstance(e, ENum) and e.val != 0:
        raise ExpIsNotWf(e, e, "nonzero numerical constant in state position")
    if isinstance(e, EMakeMap2) and is_collection(e.type.v):
        all_collections = [sv for sv in state_vars if is_collection(sv.type)]
        total_size = ENum(0).with_type(INT)
        for c in all_collections:
            total_size = EBinOp(total_size, "+", EUnaryOp(UOp.Length, c).with_type(INT)).with_type(INT)
        my_size = EUnaryOp(UOp.Length, EFlatMap(EUnaryOp(UOp.Distinct, e.e).with_type(e.e.type), e.value).with_type(e.type.v)).with_type(INT)
        s = EImplies(
            assumptions,
            EBinOp(total_size, ">=", my_size).with_type(BOOL))
        if not valid(s, collection_depth=3):
            # from cozy.evaluation import eval
            # from cozy.solver import satisfy
            # model = satisfy(EAll([assumptions, EBinOp(total_size, "<", my_size).with_type(BOOL)]), collection_depth=3, validate_model=True)
            # assert model is not None
            # raise ExpIsNotWf(e, e, "non-polynomial-sized map ({}); total_size={}, this_size={}".format(model, eval(total_size, model), eval(my_size, model)))
            raise ExpIsNotWf(e, e, "non-polynomial-sized map")

@typechecked
def exp_wf(e : Exp, state_vars : {EVar}, args : {EVar}, pool = RUNTIME_POOL, assumptions : Exp = T):
    """
    Returns True or throws exception indicating why `e` is not well-formed.
    """
    for (a, x, r, bound, p) in enumerate_fragments_and_pools(e):
        p = p if pool == RUNTIME_POOL else STATE_POOL
        try:
            exp_wf_nonrecursive(x, state_vars, args, p, assumptions=EAll(itertools.chain(a, (assumptions,))))
        except ExpIsNotWf as exc:
            raise ExpIsNotWf(e, x, exc.reason)
    return True