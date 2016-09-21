from collections import defaultdict

from syntax import *
from common import Visitor, FrozenDict

class HoleException(Exception):
    def __init__(self, hole, env):
        self.hole = hole
        self.env = env

class hashable_defaultdict(defaultdict):
    def __init__(self, k):
        super().__init__(k)
    def __hash__(self):
        return hash(tuple(sorted(self.items())))
    def __repr__(self):
        return repr(dict(self))
    def __str__(self):
        return repr(self)

class Evaluator(Visitor):
    def visit_EVar(self, v, env):
        return env[v.id]
    def visit_EHole(self, e, env):
        raise HoleException(e, dict(env))
    def visit_ENum(self, n, env):
        return n.val
    def visit_EBool(self, b, env):
        return b.val
    def visit_EEnumEntry(self, val, env):
        # return val.type.cases.index(val.name)
        return val.name
    def visit_EGetField(self, e, env):
        lhs = self.visit(e.e, env)
        if isinstance(e.e.type, THandle):
            assert e.f == "val"
            return lhs[1]
        return lhs[e.f]
    def visit_EUnaryOp(self, e, env):
        if e.op == "not":
            return not self.visit(e.e, env)
        elif e.op == "sum":
            return sum(self.visit(e.e, env))
        else:
            raise NotImplementedError(e.op)
    def visit_EBinOp(self, e, env):
        if e.op == "and":
            return self.visit(e.e1, env) and self.visit(e.e2, env)
        elif e.op == "or":
            return self.visit(e.e1, env) or self.visit(e.e2, env)
        elif e.op == "==":
            return self.visit(e.e1, env) == self.visit(e.e2, env)
        elif e.op == "+":
            return self.visit(e.e1, env) + self.visit(e.e2, env)
        else:
            raise NotImplementedError(e.op)
    def visit_ETuple(self, e, env):
        return tuple(self.visit(ee, env) for ee in e.es)
    def visit_ETupleGet(self, e, env):
        tup = self.visit(e.e, env)
        return tup[e.n]
    def visit_EApp(self, e, env):
        return self.eval_lambda(e.f, self.visit(e.arg, env), env)
    def visit_EListComprehension(self, e, env):
        return tuple(self.visit_clauses(e.clauses, e.e, env))
    def eval_lambda(self, lam, arg, env):
        env2 = dict(env)
        env2[lam.arg.id] = arg
        return self.visit(lam.body, env2)
    def visit_EMakeMap(self, e, env):
        im = defaultdict(tuple)
        for x in self.visit(e.e, env):
            im[self.eval_lambda(e.key, x, env)] += (x,)
        res = hashable_defaultdict(lambda: self.eval_lambda(e.value, (), env))
        for (k, es) in im.items():
            res[k] = self.eval_lambda(e.value, es, env)
        return res
    def visit_EMapGet(self, e, env):
        return self.visit(e.map, env)[self.visit(e.key, env)]
    def visit_EMap(self, e, env):
        return tuple(self.eval_lambda(e.f, x, env) for x in self.visit(e.e, env))
    def visit_EFilter(self, e, env):
        return tuple(x for x in self.visit(e.e, env) if self.eval_lambda(e.p, x, env))
    def visit_clauses(self, clauses, e, env):
        if not clauses:
            yield self.visit(e, env)
            return
        c = clauses[0]
        if isinstance(c, CCond):
            if self.visit(c.e, env):
                yield from self.visit_clauses(clauses[1:], e, env)
        elif isinstance(c, CPull):
            for x in self.visit(c.e, env):
                env2 = dict(env)
                env2[c.id] = x
                yield from self.visit_clauses(clauses[1:], e, env2)
    def visit_Exp(self, e, env):
        raise NotImplementedError("eval({})".format(e))
    def visit_object(self, o, *args):
        raise Exception("cannot eval {}".format(repr(o)))
    def visit(self, o, *args):
        try:
            return super().visit(o, *args)
        except:
            print("evaluation of {} failed".format(repr(o)))
            raise

def eval(e, env):
    return Evaluator().visit(e, env)

def mkval(type):
    """
    Produce an arbitrary value of the given type.
    """
    if isinstance(type, TInt) or isinstance(type, TLong):
        return 0
    if isinstance(type, TBool):
        return False
    if isinstance(type, TBag):
        return ()
    if isinstance(type, TMap):
        return hashable_defaultdict(int)
    if isinstance(type, TEnum):
        return type.cases[0]
    if isinstance(type, TRecord):
        return FrozenDict({ f:mkval(t) for (f, t) in type.fields })
    if isinstance(type, THandle):
        return (0, mkval(type.value_type))
    if isinstance(type, TTuple):
        return tuple(mkval(t) for t in type.ts)
    raise NotImplementedError(type)

class EnvCollector(Evaluator):
    def __init__(self, hole_name):
        self.hole_name = hole_name
        self.envs = []
    def visit_EHole(self, e, env):
        if e.name == self.hole_name:
            self.envs.append(dict(env))
        return mkval(e.type)

def all_envs_for_hole(e, env, hole_name):
    x = EnvCollector(hole_name)
    x.visit(e, env)
    return x.envs
