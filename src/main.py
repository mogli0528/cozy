#!/usr/bin/env python

"""
Main entry point for synthesis. Run with --help for options.
"""

from __future__ import print_function
import sys
import argparse

import parse
import compile
import common
import typecheck
import syntax
import target_syntax
import syntax_tools
import synth2

def read_file(f):
    with open(f, "r"):
        return f.read()

def run():
    parser = argparse.ArgumentParser(description='Data structure synthesizer.')

    java_opts = parser.add_argument_group("Java codegen")
    java_opts.add_argument("--java", metavar="FILE.java", default=None, help="Output file for java classes, use '-' for stdout")

    parser.add_argument("file", nargs="?", default=None, help="Input file (omit to use stdin)")
    args = parser.parse_args()

    input_text = sys.stdin.read() if args.file is None else read_file(args.file)
    ast = parse.parse(input_text)

    errors = typecheck.typecheck(ast)
    if errors:
        for e in errors:
            print("Error: {}".format(e))
        sys.exit(1)

    print(ast)
    print()
    print(syntax_tools.pprint(ast))

    # gather root types
    types = syntax_tools.all_types(ast)

    # rewrite enums
    enum_types = [t for t in types if isinstance(t, syntax.TEnum)]
    repl = {
        name : syntax.EEnumEntry(name).with_type(t)
        for t in enum_types
        for name in t.cases }
    ast = syntax_tools.subst(ast, repl)

    # synthesis
    import synth_core
    qs = [q for q in ast.methods if isinstance(q, syntax.Query) if q.name == "pendingEntries"]
    # qs = [q for q in ast.methods if isinstance(q, syntax.Query)]
    assert len(qs) > 0
    res_type = syntax.TTuple(tuple(q.ret.type) for q in qs) if len(qs) > 1 else qs[0].ret.type

    common_roots = list(repl.values())
    state_roots = []
    for (name, t) in ast.statevars:
        state_roots.append(syntax.EVar(name).with_type(t))

    basic_types = [t for t in types if not isinstance(t, syntax.TBag)]
    class TopLevelBuilder(synth_core.Builder):
        def __init__(self):
            super().__init__((), basic_types)
            self.args_by_q = { q.name: [syntax.EVar(common.fresh_name(name)).with_type(t) for (name, t) in q.args] for q in qs }
            self.state_var_name = common.fresh_name("state")
            self.state_hole_name = common.fresh_name("state")
        def build(self, cache, size):
            for state_type in self.enum_types(size - 1):
                state_var = syntax.EVar(self.state_var_name).with_type(state_type)
                state_hole = synth_core.EHole(self.state_hole_name, state_type, synth_core.Builder(common_roots + state_roots, basic_types))

                out = []
                for q in qs:
                    args = self.args_by_q[q.name]
                    q_hole = synth_core.EHole(q.name, q.ret.type, synth_core.Builder(common_roots + args + [state_var], basic_types))
                    out.append(q_hole)

                yield target_syntax.EApp(
                    target_syntax.ELambda(state_var, syntax.ETuple(tuple(out)) if len(out) > 1 else out[0]),
                    state_hole).with_type(res_type)

    builder = TopLevelBuilder()
    hole = synth_core.EHole(common.fresh_name(), res_type, builder)
    target = tuple(syntax_tools.subst(q.ret, { a1name:a2 for ((a1name, type), a2) in zip(q.args, builder.args_by_q[q.name]) }) for q in qs)
    if len(target) == 1:
        target = target[0]
    else:
        target = syntax.ETuple(target)
    spec = syntax.EBinOp(hole, "==", target)
    print(syntax_tools.pprint(spec))

    for mapping in synth_core.synth(spec):

        print("SOLUTION")
        hole = synth_core.EHole(builder.state_hole_name, None, None)
        type = mapping[hole.name].type
        result = synth_core.expand(hole, mapping)
        print("{} : {} = {}".format(
            builder.state_var_name,
            syntax_tools.pprint(type),
            syntax_tools.pprint(result)))

        for q in qs:
            hole = synth_core.EHole(q.name, q.ret.type, None)
            result = synth_core.expand(hole, mapping)
            print("{} =".format(q.name))
            print("  {}".format(syntax_tools.pprint(result)))

        return

    # if args.java is not None:
    #     with common.open_maybe_stdout(args.java) as out:
    #         out.write(compile.JavaPrinter().visit(ast))

if __name__ == "__main__":
    run()
