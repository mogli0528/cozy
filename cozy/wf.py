"""Well-formedness tests for Cozy expressions."""

import itertools

from cozy.common import No, typechecked, OrderedSet, unique
from cozy.syntax import Exp, EVar, EAll, ETRUE
from cozy.target_syntax import EStateVar
from cozy.syntax_tools import pprint, strip_EStateVar, freshen_binders, alpha_equivalent, replace
from cozy.solver import ModelCachingSolver
from cozy.pools import RUNTIME_POOL, STATE_POOL
from cozy.structures import extension_handler
from cozy.contexts import Context, all_subexpressions_with_context_information
from cozy.logging import task

class ExpIsNotWf(No):
    """An explanation for why an expression is not well-formed.

    This object is falsy so that it can be used elegantly in conditionals:

        if exp_wf(e, ...):
            <e is definitely well-formed>
    """
    def __init__(self, toplevel_expression, offending_subexpression, reason):
        super().__init__("at {}: {}".format(pprint(offending_subexpression), reason))
        self.toplevel_expression = toplevel_expression
        self.offending_subexpression = offending_subexpression
        self.reason = reason
    def __repr__(self):
        return "ExpIsNotWf({!r}, {!r}, {!r})".format(
            self.toplevel_expression,
            self.offending_subexpression,
            self.reason)

def exp_wf_nonrecursive(solver, e : Exp, context : Context, pool = RUNTIME_POOL, assumptions : Exp = ETRUE):
    """Check the well-formedness of `e` but do not recurse into its children.

    Returns True or an instance of No explaining why `e` is not well-formed.

    See `exp_wf` for an explanation of well-formedness and the parameters that
    this function requires.
    """

    if hasattr(e, "_wf"):
        return True

    state_vars = OrderedSet(v for v, p in context.vars() if p == STATE_POOL)
    args       = OrderedSet(v for v, p in context.vars() if p == RUNTIME_POOL)

    h = extension_handler(type(e))
    if h is not None:
        assumptions = EAll([assumptions, context.path_condition()])
        msg = h.check_wf(e, state_vars=state_vars, args=args, pool=pool, assumptions=assumptions, is_valid=solver.valid)
        if msg is not None:
            return No(msg)
        e._wf = True
        return True

    at_runtime = pool == RUNTIME_POOL
    if isinstance(e, EStateVar) and not at_runtime:
        return No("EStateVar in state pool position")
    if isinstance(e, EVar):
        if at_runtime and e in state_vars:
            return No("state var at runtime")
        elif not at_runtime and e in args:
            return No("arg in state exp")

    e._wf = True
    return True

@typechecked
def exp_wf(e : Exp, context : Context, pool = RUNTIME_POOL, assumptions : Exp = ETRUE, solver = None):
    """Check the well-formedess of `e`.

    Returns True or an instance of ExpIsNotWf that indicates why `e` is not
    well-formed.

    Parameters:
        e - an expression to check
        context - a context describing e's variables
        pool - what pool e lives in
        assumptions - facts that are true whenever e begins executing
            (NOTE: this does NOT need to include the path conditions from the
            context, but it is fine if it does.)
        solver - a ModelCachingSolver to use for solving formulas

    This function requires that:
     - all free variables in `e` are used in the correct pool
     - EStateVar only occurs in runtime expressions
    """
    if solver is None:
        solver = ModelCachingSolver(vars=[], funcs={})
    for x, ctx, p in all_subexpressions_with_context_information(e, context, pool):
        is_wf = exp_wf_nonrecursive(solver, x, ctx, p, assumptions=ctx.adapt(assumptions, context))
        if not is_wf:
            if isinstance(is_wf, No):
                return ExpIsNotWf(e, x, is_wf.msg)
            return is_wf
    return True

@typechecked
def repair_well_formedness(e : Exp, context : Context, extra_available_state : [Exp] = []) -> Exp:
    """Repair the EStateVar nodes in an expression that is not well-formed.

    Parameters:
        e                     - the expression to repair
        context               - the intended context for e
        extra_available_state - extra state expressions that e can use

    Assuming that all expressions in extra_available_state are well-formed
    state expressions, the output will be a well-formed runtime expression that
    behaves like `e`.
    """

    with task("repairing"):

        e = strip_EStateVar(e)

        # state expressions in decreasing order of size
        available_state = sorted(unique(itertools.chain(
            (v for v, p in context.vars() if p == STATE_POOL),
            extra_available_state)), key=lambda e: -e.size())

        with task("making replacements", size=e.size()):
            for s in available_state:
                e = replace(e, s, EStateVar(s).with_type(s.type),
                    match=alpha_equivalent,
                    filter=lambda e: not isinstance(e, EStateVar))

        with task("freshening binders"):
            e = freshen_binders(e, context)

        with task("checking correctness"):
            res = exp_wf(e, context, RUNTIME_POOL)
            assert res, str(res)

        return e
