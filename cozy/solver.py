from collections import defaultdict
import itertools

import z3

from cozy.target_syntax import *
from cozy.syntax_tools import pprint, free_vars
from cozy.common import declare_case, fresh_name, Visitor, FrozenDict, typechecked, memoize
from cozy import evaluation

# TODO: Int==Bv32, Long==Bv64
TBitVec = declare_case(Type, "TBitVec", ["width"])

class _SymbolicUnion(object):
    """
    Represents `If(cond, x, y)` expression
    """
    def __init__(self, cond, x, y):
        self.cond = cond
        self.lhs = x
        self.rhs = y
    def map(self, f):
        new_lhs = fmap(self.lhs, f)
        new_rhs = fmap(self.rhs, f)
        if isinstance(new_lhs, z3.AstRef) and isinstance(new_rhs, z3.AstRef):
            return z3.If(self.cond, new_lhs, new_rhs, self.cond.ctx_ref())
        return _SymbolicUnion(self.cond, new_lhs, new_rhs)
    def __repr__(self):
        return "SymbolicUnion({}, {}, {})".format(repr(self.cond), repr(self.lhs), repr(self.rhs))

@typechecked
def SymbolicUnion(ty : Type, cond : z3.AstRef, then_branch, else_branch):
    if decideable(ty):
        return z3.If(cond, then_branch, else_branch, cond.ctx_ref())
    else:
        return _SymbolicUnion(cond, then_branch, else_branch)

def fmap(x, f):
    if isinstance(x, _SymbolicUnion):
        return x.map(f)
    return f(x)

class ToZ3(Visitor):
    def __init__(self, z3ctx, z3solver):
        self.ctx = z3ctx
        self.solver = z3solver
        self.funcs = { }
    def eq(self, t, e1, e2, env):
        return fmap(e1, lambda v1:
               fmap(e2, lambda v2:
               self._eq(t, v1, v2, env)))
    def _eq(self, t, e1, e2, env):
        if type(t) in [TInt, TLong, TBool, TEnum, TNative, TString]:
            return e1 == e2
        elif isinstance(t, TMaybe):
            if (e1 is None) and (e2 is None):
                return z3.BoolVal(True, self.ctx)
            if (e1 is None) != (e2 is None):
                return z3.BoolVal(False, self.ctx)
            return self.eq(t.t, e1, e2, env)
        elif isinstance(t, TBag):
            elem_type = t.t
            lhs_mask, lhs_elems = e1
            rhs_mask, rhs_elems = e2

            # n = max(len(lhs_elems), len(rhs_elems))

            # lengths equal... might not be necessary
            e1len = self.len_of(e1)
            e2len = self.len_of(e2)
            conds = []
            conds.append(e1len == e2len)

            lhs_counts = [ (x, self.count_in(elem_type, e1, x, env)) for x in lhs_elems ]
            for x, count in lhs_counts:
                conds.append(count == self.count_in(elem_type, e2, x, env))

            rhs_counts = [ (x, self.count_in(elem_type, e1, x, env)) for x in rhs_elems ]
            for x, count in rhs_counts:
                conds.append(count == self.count_in(elem_type, e1, x, env))

            return z3.And(*conds, self.ctx)
        elif isinstance(t, THandle):
            h1, val1 = e1
            h2, val2 = e2
            return h1 == h2
        elif isinstance(t, TRecord):
            conds = [self.eq(tt, e1[f], e2[f], env) for (f, tt) in t.fields]
            return z3.And(*conds, self.ctx)
        elif isinstance(t, TTuple):
            conds = [self.eq(t, x, y, env) for (t, x, y) in zip(t.ts, e1, e2)]
            return z3.And(*conds, self.ctx)
        else:
            raise NotImplementedError(t)
    def count_in(self, t, bag, x, env):
        """
        t - type of elems in bag
        bag - concrete (non-SymbolicUnion) bag
        x - elem
        env - environment

        returns # of times x appears in bag
        """
        bag_mask, bag_elems = bag
        l = 0
        for i in range(len(bag_elems)):
            l = z3.If(z3.And(bag_mask[i], self.eq(t, x, bag_elems[i], env), self.ctx), 1, 0, ctx=self.ctx) + l
        return l
    def len_of(self, val):
        bag_mask, bag_elems = val
        l = 0
        for i in range(len(bag_elems)):
            l = z3.If(bag_mask[i], 1, 0, ctx=self.ctx) + l
        return l
    def visit_TInt(self, t):
        return z3.IntSort(self.ctx)
    def visit_TLong(self, t):
        return z3.IntSort(self.ctx)
    def visit_TString(self, t):
        return z3.IntSort(self.ctx)
    def visit_TNative(self, t):
        return z3.IntSort(self.ctx)
    def visit_TBool(self, t):
        return z3.BoolSort(self.ctx)
    def visit_Type(self, t):
        raise NotImplementedError(t)
    def visit_EVar(self, v, env):
        return env[v.id]
    def visit_ENum(self, n, env):
        if n.type == TInt():
            return z3.IntVal(n.val, self.ctx)
        raise NotImplementedError(n.type)
    def visit_EBool(self, b, env):
        return z3.BoolVal(b.val, self.ctx)
    def visit_EEmptyList(self, e, env):
        return ([], [])
    def visit_ESingleton(self, e, env):
        return ([z3.BoolVal(True, self.ctx)], [self.visit(e.e, env)])
    def visit_EJust(self, e, env):
        return self.visit(e.e, env)
    def flatten(self, e, env):
        if decideable(e.type):
            yield (self.visit(e, env), e.type)
        else:
            raise NotImplementedError(e.type)
    def visit_ECall(self, call, env):
        args = [x for arg in call.args for x in self.flatten(arg, env)]
        key = (call.func, call.type, tuple(t for (v, t) in args))
        f = self.funcs.get(key)
        if f is None:
            f = z3.Function(fresh_name(call.func), *[self.visit(t) for (v, t) in args], self.visit(call.type))
            self.funcs[key] = f
        return f(*[v for (v, t) in args])
    def visit_EEnumEntry(self, e, env):
        return e.type.cases.index(e.name)
    def visit_ETuple(self, e, env):
        return tuple(self.visit(ee, env) for ee in e.es)
    def visit_ETupleGet(self, e, env):
        tup = self.visit(e.e, env)
        return fmap(tup, lambda tup: tup[e.n])
    def visit_EAlterMaybe(self, e, env):
        return fmap(self.visit(e.e, env),
            lambda res: self.apply(e.f, res, env) if res is not None else res)
    def visit_EFlatten(self, e, env):
        def go(bag):
            mask, elems = bag
            if not mask:
                return bag
            def recurse(sub_bag):
                exists = mask[0]
                sub_mask, sub_elems = sub_bag
                return fmap(go((mask[1:], elems[1:])),
                    lambda rest: ([z3.And(exists, m, self.ctx) for m in sub_mask] + rest[0], sub_elems + rest[1]))
            return fmap(elems[0], recurse)
        flat = fmap(self.visit(e.e, env), go)
        # print("bag = {}".format(self.visit(e.e, env)))
        # print("flat = {}".format(flat))
        return flat
    def visit_EFlatMap(self, e, env):
        return self.visit(EFlatten(EMap(e.e, e.f)), env)
    def visit_ECond(self, e, env):
        cond = self.visit(e.cond, env)
        then_branch = self.visit(e.then_branch, env)
        else_branch = self.visit(e.else_branch, env)
        return SymbolicUnion(e.type, cond, then_branch, else_branch)
    def visit_EUnaryOp(self, e, env):
        if e.op == "not":
            return z3.Not(self.visit(e.e, env), ctx=self.ctx)
        elif e.op == "sum":
            def take_sum(bag):
                bag_mask, bag_elems = bag
                sum = z3.IntVal(0, self.ctx)
                for i in range(len(bag_elems)):
                    sum = z3.If(bag_mask[i], bag_elems[i], z3.IntVal(0, self.ctx), ctx=self.ctx) + sum
                return sum
            return fmap(self.visit(e.e, env), take_sum)
        elif e.op == "unique":
            def is_unique(bag):
                bag_mask, bag_elems = bag
                rest = (bag_mask[1:], bag_elems[1:])
                if bag_elems:
                    return z3.And(
                        z3.Implies(bag_mask[0], self.count_in(e.e.type.t, rest, bag_elems[0], env) == 0, self.ctx),
                        is_unique(rest),
                        self.ctx)
                else:
                    return z3.BoolVal(True, self.ctx)
            return fmap(self.visit(e.e, env), is_unique)
        elif e.op == "len":
            return fmap(self.visit(e.e, env), self.len_of)
        elif e.op == "the":
            assert isinstance(e.type, TMaybe)
            def get_first(bag):
                bag_mask, bag_elems = bag
                if not bag_elems:
                    return None
                rest = (bag_mask[1:], bag_elems[1:])
                return SymbolicUnion(e.type, bag_mask[0], bag_elems[0], get_first(rest))
            return fmap(self.visit(e.e, env), get_first)
        else:
            raise NotImplementedError(e.op)
    def visit_EGetField(self, e, env):
        r = self.visit(e.e, env)
        if isinstance(e.e.type, THandle):
            assert e.f == "val"
            h, val = r
            return val
        else:
            return r[e.f]
    def visit_EBinOp(self, e, env):
        v1 = self.visit(e.e1, env)
        v2 = self.visit(e.e2, env)
        if e.op == "and":
            return z3.And(v1, v2, self.ctx)
        elif e.op == "or":
            return z3.Or(v1, v2, self.ctx)
        elif e.op == "==":
            return self.eq(e.e1.type, v1, v2, env)
        elif e.op == ">":
            return v1 > v2
        elif e.op == "<":
            return v1 < v2
        elif e.op == ">=":
            return v1 >= v2
        elif e.op == "<=":
            return v1 <= v2
        elif e.op == "+":
            if isinstance(e.type, TBag):
                return fmap(v1, lambda bag1:
                       fmap(v2, lambda bag2:
                       (bag1[0] + bag2[0], bag1[1] + bag2[1])))
            return v1 + v2
        elif e.op == "-":
            return v1 - v2
        else:
            raise NotImplementedError(e.op)
    def visit_EListComprehension(self, e, env):
        x = self.visit_clauses(e.clauses, e.e, env)
        # print("{} ==> {}".format(pprint(e), x))
        return self.visit_clauses(e.clauses, e.e, env)
    def visit_EMap(self, e, env):
        def go(bag):
            bag_mask, bag_elems = bag
            res_elems = []
            for x in bag_elems:
                res_elems.append(self.apply(e.f, x, env))
            return bag_mask, res_elems
        return fmap(self.visit(e.e, env), go)
    def do_filter(self, bag, p, env):
        return self.raw_filter(bag, lambda x: self.apply(p, x, env))
    def raw_filter(self, bag, p):
        bag_mask, bag_elems = bag
        res_mask = []
        for mask, x in zip(bag_mask, bag_elems):
            res_mask.append(z3.And(mask, p(x), self.ctx))
        return res_mask, bag_elems
    def visit_EFilter(self, e, env):
        return fmap(self.visit(e.e, env), lambda bag: self.do_filter(bag, e.p, env))
    def visit_EMakeMap(self, e, env):
        def go(bag):
            bag_mask, bag_elems = bag
            ks = [ self.apply(e.key, x, env) for x in bag_elems ]
            x = EVar(fresh_name()).with_type(e.e.type.t)
            m = {"mapping": [(k, self.apply(
                    e.value,
                    self.raw_filter(bag, lambda x: self.eq(e.key.body.type, self.apply(e.key, x, env), k, env)),
                    env)) for k in ks],
                "default": e.value}
            return m
        return fmap(self.visit(e.e, env), go)
    def visit_EMapGet(self, e, env):
        key = self.visit(e.key, env)
        def go(map):
            res = self.apply(map["default"], ([], []), env)
            # print("map get {} on {}".format(key, map))
            for (k, v) in map["mapping"]:
                # print("   k   = {}".format(repr(k)))
                # print("   key = {}".format(repr(key)))
                # print("   v   = {}".format(repr(v)))
                # print("   res = {}".format(repr(res)))
                res = SymbolicUnion(e.type, self.eq(e.key.type, k, key, env), v, res)
            return res
        return fmap(self.visit(e.map, env), go)
    def visit_EApp(self, e, env):
        return self.apply(e.f, self.visit(e.arg, env), env)
    def apply(self, lam, arg, env):
        env2 = dict(env)
        env2[lam.arg.id] = arg
        return self.visit(lam.body, env2)
    def visit_clauses(self, clauses, e, env):
        if not clauses:
            return [True], [self.visit(e, env)]
        c = clauses[0]
        if isinstance(c, CCond):
            bag_mask, bag_elems = self.visit_clauses(clauses[1:], e, env)
            res_mask = []
            for i in range(len(bag_elems)):
                incl_this = z3.And(bag_mask[i], self.visit(c.e, env), self.ctx)
                res_mask += [incl_this]
            return res_mask, bag_elems
        elif isinstance(c, CPull):
            bag_mask, bag_elems = self.visit(c.e, env)
            res_mask, res_elems = [], []
            for i in range(len(bag_elems)):
                incl_this = bag_mask[i]
                env2 = dict(env)
                env2[c.id] = bag_elems[i]
                bag2_mask, bag2_elems = self.visit_clauses(clauses[1:], e, env2)
                res_mask += [z3.And(incl_this, bit, self.ctx) for bit in bag2_mask]
                res_elems += bag2_elems
            return res_mask, res_elems
    def visit_Exp(self, e, *args):
        raise NotImplementedError("toZ3({})".format(e))
    def visit_AstRef(self, e, env):
        """AstRef is the Z3 AST node type"""
        return e
    def visit_bool(self, e, env):
        return z3.BoolVal(e, self.ctx)
    def visit(self, e, *args):
        try:
            return super().visit(e, *args)
        except:
            print("failed to convert {}".format(pprint(e)))
            raise

def decideable(t):
    return type(t) in [TInt, TLong, TBool, TString, TBitVec, TEnum, TNative]

def mkvar(ctx, solver, collection_depth, type, handle_vars):
    if type == TInt() or type == TLong() or isinstance(type, TNative) or type == TString():
        return z3.Int(fresh_name(), ctx=ctx)
    elif type == TBool():
        return z3.Bool(fresh_name(), ctx=ctx)
    elif isinstance(type, TBitVec):
        return z3.BitVec(fresh_name(), type.width, ctx=ctx)
    elif isinstance(type, TEnum):
        ncases = len(type.cases)
        n = z3.Int(fresh_name(), ctx=ctx)
        solver.add(n >= 0)
        solver.add(n < ncases)
        return n
    elif isinstance(type, TBag):
        mask = [mkvar(ctx, solver, collection_depth, TBool(), handle_vars) for i in range(collection_depth)]
        elems = [mkvar(ctx, solver, collection_depth, type.t, handle_vars) for i in range(collection_depth)]
        # symmetry breaking
        for i in range(len(mask) - 1):
            solver.add(z3.Implies(mask[i], mask[i+1], ctx))
        return (mask, elems)
    elif isinstance(type, TRecord):
        return { field : mkvar(ctx, solver, collection_depth, t, handle_vars) for (field, t) in type.fields }
    elif isinstance(type, THandle):
        h = z3.Int(fresh_name(), ctx)
        v = (h, mkvar(ctx, solver, collection_depth, type.value_type, handle_vars))
        handle_vars.append((type.value_type,) + v)
        return v
    else:
        raise NotImplementedError(type)

def mkconst(ctx, solver, val):
    if type(val) == int:
        return z3.IntVal(val, ctx)
    elif type(val) == bool:
        return z3.BoolVal(val, ctx)
    elif type(val) == tuple:
        return ([z3.BoolVal(True, ctx) for x in val], [mkconst(ctx, solver, x) for x in val])
    else:
        raise NotImplementedError(repr(val))

def satisfy(e, vars = None, collection_depth : int = 2, validate_model : bool = True):
    # print("sat? {}".format(pprint(e)))
    assert e.type == TBool()

    ctx = z3.Context()
    solver = z3.Solver(ctx=ctx)
    solver.set("core.validate", validate_model)
    visitor = ToZ3(ctx, solver)

    def reconstruct(model, value, type):
        if type == TInt() or type == TLong():
            return model.eval(value, model_completion=True).as_long()
        elif isinstance(type, TNative):
            return (type.name, model.eval(value, model_completion=True).as_long())
        elif type == TString():
            i = model.eval(value, model_completion=True).as_long()
            s = "b"
            if i >= 0:
                s += "b" * i
            else:
                s = "a" * (-i) + s
            return s
        elif type == TBool():
            return bool(model.eval(value, model_completion=True))
        elif isinstance(type, TBitVec):
            return model.eval(value, model_completion=True).as_long()
        elif isinstance(type, TBag):
            mask, elems = value
            real_val = []
            for i in range(len(elems)):
                if reconstruct(model, mask[i], TBool()):
                    real_val.append(reconstruct(model, elems[i], type.t))
            return evaluation.Bag(real_val)
        elif isinstance(type, TEnum):
            val = model.eval(value, model_completion=True).as_long()
            return type.cases[val]
        elif isinstance(type, THandle):
            id, val = value
            id = reconstruct(model, id, TInt())
            val = reconstruct(model, val, type.value_type)
            return (id, val)
        elif isinstance(type, TRecord):
            res = defaultdict(lambda: None)
            for (field, t) in type.fields:
                res[field] = reconstruct(model, value[field], t)
            return FrozenDict(res)
        else:
            raise NotImplementedError(type)

    def unreconstruct(value, type):
        """Converts reconstructed value back to a Z3 value"""
        if type == TInt() or type == TLong():
            return z3.IntVal(value, ctx)
        elif isinstance(type, TNative):
            return z3.IntVal(value[1], ctx)
        else:
            raise NotImplementedError(type)

    _env = { }
    fvs = vars if vars is not None else free_vars(e)
    handle_vars = []
    for v in fvs:
        # print("{} : {}".format(pprint(v), pprint(v.type)))
        _env[v.id] = mkvar(ctx, solver, collection_depth, v.type, handle_vars)
    # print(_env)

    # Handles implement reference equality... so if the references are the same,
    # the values must be also. TODO: we could eliminiate the need for this by
    # encoding handles as ints plus an uninterpreted "read_value" function for
    # each handle type.
    for i in range(len(handle_vars)):
        for j in range(i + 1, len(handle_vars)):
            h1type, h1, v1 = handle_vars[i]
            h2type, h2, v2 = handle_vars[j]
            if h1type == h2type:
                solver.add(z3.Implies(h1 == h2, visitor.eq(h1type, v1, v2, _env), ctx))

    solver.add(visitor.visit(e, _env))
    # print(solver.assertions())
    res = solver.check()
    if res == z3.unsat:
        return None
    elif res == z3.unknown:
        raise Exception("z3 reported unknown")
    else:
        model = solver.model()
        # print(model)
        res = { }
        for v in fvs:
            res[v.id] = reconstruct(model, _env[v.id], v.type)
        for k, f in visitor.funcs.items():
            name = k[0]
            out_type = k[1]
            arg_types = k[2]
            @memoize
            def extracted_func(*args):
                return reconstruct(model, f(*[unreconstruct(v, t) for (v, t) in zip(args, arg_types)]), out_type)
            res[name] = extracted_func
        # print(res)
        if validate_model:
            x = evaluation.eval(e, res)
            if x is not True:
                print("bad example: {}".format(res))
                print(" ---> got {}".format(repr(x)))
                print(" ---> model: {}".format(model))
                print(" ---> assertions: {}".format(solver.assertions()))
                raise Exception("model validation failed")
        return res

def valid(e, **opts):
    return satisfy(EUnaryOp("not", e).with_type(TBool()), **opts) is None

def feasible(spec, examples):
    return True # TODO
