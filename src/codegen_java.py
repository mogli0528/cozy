
import predicates
import plans

_TY = "Double" # eh... we'll do something else later!

class Ty(object):
    def to_java(self, record_type):
        pass
    def unify(self, other):
        pass

class HashMap(Ty):
    def __init__(self, fieldName, ty):
        self.fieldName = fieldName
        self.ty = ty
    def to_java(self, record_type):
        return "java.util.Map<{},{}>".format(_TY, self.ty.to_java(record_type))
    def unify(self, other):
        if type(other) is HashMap and other.fieldName == self.fieldName:
            return HashMap(self.fieldName, self.ty.unify(other.ty))
        raise Exception("failed to unify {} and {}".format(self, other))

class SortedSet(Ty):
    def __init__(self, fieldName):
        self.fieldName = fieldName
    def to_java(self, record_type):
        return "java.util.List<{}>".format(record_type)
    def unify(self, other):
        if type(other) is UnsortedSet:
            return self
        if type(other) is SortedSet and other.fieldName == self.fieldName:
            return self
        raise Exception("failed to unify {} and {}".format(self, other))

class UnsortedSet(Ty):
    def to_java(self, record_type):
        return "java.util.Iterable<{}>".format(record_type)
    def unify(self, other):
        if type(other) is UnsortedSet or type(other) is SortedSet:
            return other
        raise Exception("failed to unify {} and {}".format(self, other))

def write_java(fields, qvars, plan, writer):
    """
    Writes a Java data structure implementation to the given writer.
    Arguments:
     - fields (a list of field names)
     - qvars (a list of query var names)
     - plan (an execution plan)
     - writer (a function that consumes strings)
    """

    record_type_name = "Record"
    structure_name = "DataStructure"

    members = [] # will be filled with (name,ty) tuples

    def onMember(ty):
        name = _fresh_name()
        members.append((name, ty))
        return name

    proc, result = _traverse(fields, qvars, plan, record_type_name, UnsortedSet(), onMember)

    writer("public class {} {{\n".format(structure_name))

    writer("""
    private static <T> void insert_sorted(java.util.List<T> l, T x, java.util.Comparator<T> cmp) {
        int idx = java.util.Collections.binarySearch(l, x, cmp);
        if (idx < 0) { idx = -(idx + 1); }
        l.add(idx, x);
    }
    private static abstract class FilteredIterable<T> implements Iterable<T> {
        private final Iterable<T> wrapped;
        public FilteredIterable(Iterable<T> wrapped) {
            this.wrapped = wrapped;
        }

        protected abstract boolean test(T x);

        @Override
        public java.util.Iterator<T> iterator() {
            final java.util.Iterator<T> it = wrapped.iterator();
            return new java.util.Iterator<T>() {
                boolean hasNext = false;
                T next = null;
                {
                    advance();
                }
                private void advance() {
                    hasNext = it.hasNext();
                    while (hasNext) {
                        next = it.next();
                        if (test(next)) {
                            break;
                        }
                        hasNext = it.hasNext();
                    }
                }
                @Override
                public boolean hasNext() {
                    return hasNext;
                }
                @Override
                public T next() {
                    T result = next;
                    advance();
                    return result;
                }
                @Override
                public void remove() {
                    throw new UnsupportedOperationException();
                }
            };
        }
    }
    private static <T> java.util.Set<T> mkset(Iterable<T> it) {
        java.util.Set<T> s = new java.util.HashSet<T>();
        for (T x : it) {
            s.add(x);
        }
        return s;
    }
    private static <T> Iterable<T> intersect(Iterable<T> left, Iterable<T> right) {
        final java.util.Set<T> s = mkset(left);
        return new FilteredIterable<T>(right) {
            @Override
            public boolean test(T x) {
                return s.contains(x);
            }
        };
    }
    private static <T> Iterable<T> union(Iterable<T> left, Iterable<T> right) {
        java.util.Set<T> s = mkset(left);
        s.addAll(mkset(right));
        return s;
    }\n""")

    _gen_record_type(record_type_name, fields, writer)

    for name, ty in members:
        writer("    private {} {} = {};\n".format(ty.to_java(record_type_name), name, new(ty, record_type_name)))

    writer("    public Iterable<{}> query({}) {{\n".format(record_type_name, ", ".join("{} {}".format(_TY, v) for v in qvars)))
    writer(proc)
    writer("        return {};\n".format(result))
    writer("    }\n")

    writer("    public void add({record_type} x) {{\n".format(record_type=record_type_name))
    for name, ty in members:
        _gen_insert(name, ty, "x", record_type_name, writer)
    writer("    }\n")

    writer("    public void remove({record_type} x) {{\n".format(record_type=record_type_name))
    writer("        throw new UnsupportedOperationException();\n")
    writer("    }\n")

    writer("}\n")

def _gen_insert(e, ty, x, record_type_name, writer):
    if type(ty) is HashMap:
        k = "{}.{}".format(x, ty.fieldName)
        tmp = _fresh_name()
        writer("        {} {} = {}.get({});\n".format(ty.ty.to_java(record_type_name), tmp, e, k))
        writer("        if ({} == null) {{\n".format(tmp))
        writer("            {} = {};\n".format(tmp, new(ty.ty, record_type_name)))
        writer("            {}.put({}, {});\n".format(e, k, tmp))
        writer("        }\n")
        _gen_insert(tmp, ty.ty, x, record_type_name, writer)
    elif type(ty) is SortedSet:
        writer("        insert_sorted({}, {}, new java.util.Comparator<{record_type}>() {{ public int compare({record_type} a, {record_type} b) {{ return a.{field}.compareTo(b.{field}); }} }});\n".format(e, x, record_type=record_type_name, field=ty.fieldName))
    elif type(ty) is UnsortedSet:
        writer("        {}.add({});\n".format(e, x))

def new(ty, record_type_name):
    if type(ty) is HashMap:
        return "new java.util.HashMap<{}, {}>()".format(_TY, ty.ty.to_java(record_type_name))
    elif type(ty) is SortedSet or type(ty) is UnsortedSet:
        return "new java.util.ArrayList<{}>()".format(record_type_name)

def _gen_record_type(name, fields, writer):
    writer("    public static class {} {{\n".format(name))
    for f in fields:
        writer("        public final {} {};\n".format(_TY, f))
    writer("        public {}({}) {{\n".format(name, ", ".join("{} {}".format(_TY, f) for f in fields)))
    for f in fields:
        writer("            this.{f} = {f};\n".format(f=f))
    writer("        }\n")
    writer("        @Override\n");
    writer("        public String toString() {\n")
    writer('            return new StringBuilder().append("{}(")'.format(name))
    first = True
    for f in fields:
        if not first:
            writer(".append(',')")
        writer('.append("{}=")'.format(f))
        writer(".append({})".format(f))
        first = False
    writer(".append(')').toString();\n")
    writer("        }\n")
    writer("    }\n")

_i = 0
def _fresh_name():
    global _i
    _i += 1
    return "name{}".format(_i)

def _predicate_to_exp(fields, qvars, pred, target):
    if type(pred) is predicates.Var:
        return pred.name if pred.name in qvars else "{}.{}".format(target, pred.name)
    elif type(pred) is predicates.Bool:
        return "true" if pred.val else "false"
    elif type(pred) is predicates.Compare:
        return "({}) {} ({})".format(
            _predicate_to_exp(fields, qvars, pred.lhs, target),
            predicates.opToStr(pred.op),
            _predicate_to_exp(fields, qvars, pred.rhs, target))
    elif type(pred) is predicates.And:
        return "({}) && ({})".format(
            _predicate_to_exp(fields, qvars, pred.lhs, target),
            _predicate_to_exp(fields, qvars, pred.rhs, target))
    elif type(pred) is predicates.Or:
        return "({}) || ({})".format(
            _predicate_to_exp(fields, qvars, pred.lhs, target),
            _predicate_to_exp(fields, qvars, pred.rhs, target))
    elif type(pred) is predicates.Not:
        return "!({})".format(_predicate_to_exp(fields, qvars, pred.p, target))

def empty(ty, record_type_name):
    if type(ty) is HashMap:
        return "java.util.Collections.<{}, {}>emptyMap()".format(_TY, ty.ty.to_java(record_type_name))
    return "java.util.Collections.<{}>emptyList()".format(record_type_name)

def _traverse(fields, qvars, plan, record_type_name, resultTy, onMember):
    if type(plan) is plans.All:
        name = onMember(resultTy)
        return ("", name)
    elif type(plan) is plans.Empty:
        return ("", empty(resultTy, record_type_name))
    elif type(plan) is plans.HashLookup:
        p, r = _traverse(fields, qvars, plan.plan, record_type_name, HashMap(plan.fieldName, resultTy), onMember)
        n = _fresh_name()
        proc  = "        {} {} = {}.get({});\n".format(resultTy.to_java(record_type_name), n, r, plan.varName)
        proc += "        if ({n} == null) {{ {n} = {empty}; }}\n".format(n=n, empty=empty(resultTy, record_type_name))
        return (p + proc, n)
    elif type(plan) is plans.BinarySearch:
        resultTy = resultTy.unify(SortedSet(plan.fieldName))
        p, r = _traverse(fields, qvars, plan.plan, record_type_name, resultTy, onMember)
        start = _fresh_name()
        end = _fresh_name()
        proc = "        int {}, {};\n".format(start, end)

        def bisect(op, dst):
            """Generates code to set `dst` such that tmp[0:dst] `op` varName and not (tmp[dst:] `op` varName)."""
            return """
        int {lo} = 0;
        int {hi} = {tmp}.size();
        while ({lo} < {hi}) {{
            int {mid} = ({lo} >> 1) + ({hi} >> 1) + ({lo} & {hi} & 1); // overflow-free average
            if ({tmp}.get({mid}).{fieldName} {op} {varName}) {{
                {lo} = {mid} + 1;
            }} else {{
                {hi} = {mid};
            }}
        }}
        {dst} = {lo};\n""".format(
                lo=_fresh_name(),
                hi=_fresh_name(),
                mid=_fresh_name(),
                dst=dst,
                op=op,
                tmp=r,
                fieldName=plan.fieldName,
                varName=plan.varName)

        if plan.op is plans.Eq:
            proc += bisect("<", start)
            proc += bisect("<=", end)
        elif plan.op is plans.Lt:
            proc += "        {} = 0;\n".format(end)
            proc += bisect("<", end)
        elif plan.op is plans.Le:
            proc += "        {} = 0;\n".format(start)
            proc += bisect("<=", end)
        elif plan.op is plans.Gt:
            proc += bisect("<=", start)
            proc += "        {} = {}.size();\n".format(end, r)
        elif plan.op is plans.Ge:
            proc += bisect("<", start)
            proc += "        {} = {}.size();\n".format(end, r)
        return (p + proc, "{}.subList({}, {})".format(r, start, end))
    elif type(plan) is plans.Filter:
        p, r = _traverse(fields, qvars, plan.plan, record_type_name, resultTy, onMember)
        n = _fresh_name()
        proc = """
        Iterable<{ty}> {n} = new FilteredIterable<{ty}>({r}) {{
            @Override
            public boolean test({ty} x) {{
                return {pred};
            }}
        }};\n""".format(r=r, n=n, pred=_predicate_to_exp(fields, qvars, plan.predicate, "x"), ty=record_type_name)
        return (p + proc, n)
    elif type(plan) is plans.Intersect:
        p1, r1 = _traverse(fields, qvars, plan.plan1, record_type_name, resultTy, onMember)
        p2, r2 = _traverse(fields, qvars, plan.plan2, record_type_name, resultTy, onMember)
        return (p1 + p2, "intersect({}, {})".format(r1, r2))
    elif type(plan) is plans.Union:
        p1, r1 = _traverse(fields, qvars, plan.plan1, record_type_name, resultTy, onMember)
        p2, r2 = _traverse(fields, qvars, plan.plan2, record_type_name, resultTy, onMember)
        return (p1 + p2, "union({}, {})".format(r1, r2))
