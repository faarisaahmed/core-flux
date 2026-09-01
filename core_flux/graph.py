"""A minimal FFmpeg filtergraph builder.

This is the layer that used to be provided by ``ffmpeg-python``. Owning it
removes the project's only dependency and fixes two things that library got
wrong for our purposes:

* **Fan-out.** Feeding one stream into two filters is legal FFmpeg (it needs a
  ``split``), but ``ffmpeg-python`` raises instead. Here the split is inserted
  automatically at serialisation time.
* **Input reuse.** Two layers reading the same file used to decode it twice.
  Identical inputs are merged into a single decode, which is strictly less work.

Nothing here knows about video editing; it only knows nodes, streams, and how
to render them into an argument list.
"""

# Characters that terminate a value inside a filtergraph and must be escaped.
_SPECIAL = ["\\", "'", ":", ",", ";", "[", "]"]


def escape_value(value):
    """Escape a filter argument value for use inside -filter_complex."""
    text = str(value)
    for char in _SPECIAL:
        text = text.replace(char, "\\" + char)
    return text


class Stream(object):
    """One labelled output of an input file or a filter.

    Streams are created once by their producer and compared by identity, which
    is what lets the graph count consumers and insert splits reliably.
    """

    __slots__ = ("source", "index", "kind")

    def __init__(self, source, index, kind):
        self.source = source
        self.index = index
        self.kind = kind  # "v" or "a"

    def filter(self, name, *args, **kwargs):
        """Chain a single-input, single-output filter of the same kind."""
        node = FilterNode(name, [self], args, kwargs, output_kinds=(self.kind,))
        return node.outputs[0]

    def __repr__(self):
        return "<Stream %s[%d]>" % (self.kind, self.index)


class InputFile(object):
    """An ``-i`` argument, plus any options that must precede it."""

    def __init__(self, path, options=None):
        self.path = path
        self.options = dict(options or {})
        self._video = None
        self._audio = None

    @property
    def key(self):
        """Identity for deduplication. Computed live, because options can be
        changed after construction (loop() sets -stream_loop, for instance)."""
        return (self.path, tuple(sorted(self.options.items())))

    def video(self):
        if self._video is None:
            self._video = Stream(self, 0, "v")
        return self._video

    def audio(self):
        if self._audio is None:
            self._audio = Stream(self, 0, "a")
        return self._audio

    def args(self):
        out = []
        for name, value in self.options.items():
            out.append("-" + name)
            if value is not None:
                out.append(str(value))
        out.extend(["-i", self.path])
        return out

    def __repr__(self):
        return "<InputFile %r>" % (self.path,)


class FilterNode(object):
    """A single filter with its inputs, arguments and outputs."""

    def __init__(self, name, inputs, args=(), kwargs=None, output_kinds=("v",)):
        self.name = name
        self.inputs = list(inputs)
        self.args = list(args)
        self.kwargs = dict(kwargs or {})
        self.outputs = [
            Stream(self, index, kind) for index, kind in enumerate(output_kinds)
        ]

    def describe(self):
        """Render this filter as ``name=arg:key=value``."""
        parts = [escape_value(a) for a in self.args]
        for key in sorted(self.kwargs):
            value = self.kwargs[key]
            if value is None:
                continue
            parts.append("%s=%s" % (key, escape_value(value)))
        return "%s=%s" % (self.name, ":".join(parts)) if parts else self.name

    def __repr__(self):
        return "<FilterNode %s>" % (self.describe(),)


def multi_filter(streams, name, output_kinds=("v",), args=(), kwargs=None):
    """Apply a filter that takes several inputs; returns its output streams."""
    node = FilterNode(name, streams, args, kwargs, output_kinds=output_kinds)
    return node.outputs


class Graph(object):
    """Serialises a set of output streams into FFmpeg arguments.

    The graph holds no state: layers build their own :class:`InputFile` objects
    independently, and :meth:`build` discovers them by walking the stream tree.
    Inputs that compare equal are merged into a single decode.
    """

    @staticmethod
    def _collect(outputs):
        """Every filter node reachable from `outputs`, dependencies first."""
        ordered = []
        seen = set()

        def visit(node):
            if id(node) in seen:
                return
            seen.add(id(node))
            for stream in node.inputs:
                if isinstance(stream.source, FilterNode):
                    visit(stream.source)
            ordered.append(node)

        for stream in outputs:
            if isinstance(stream.source, FilterNode):
                visit(stream.source)
        return ordered

    @staticmethod
    def _dedupe_inputs(nodes, outputs):
        """Point every reference at one canonical InputFile per distinct file.

        Two layers reading the same path would otherwise decode it twice. Once
        they share an input, the split pass below gives each its own branch.
        """
        canonical = {}

        def canonical_stream(stream):
            source = stream.source
            if not isinstance(source, InputFile):
                return stream
            chosen = canonical.setdefault(source.key, source)
            if chosen is source:
                return stream
            return chosen.video() if stream.kind == "v" else chosen.audio()

        for node in nodes:
            node.inputs = [canonical_stream(s) for s in node.inputs]
        return [canonical_stream(s) for s in outputs]

    @staticmethod
    def _insert_splits(nodes, outputs):
        """Give every multiply-consumed stream its own split branch."""
        counts = {}
        registry = {}
        for node in nodes:
            for stream in node.inputs:
                counts[id(stream)] = counts.get(id(stream), 0) + 1
                registry[id(stream)] = stream
        for stream in outputs:
            counts[id(stream)] = counts.get(id(stream), 0) + 1
            registry[id(stream)] = stream

        branches = {}
        for stream_id, count in counts.items():
            if count < 2:
                continue
            stream = registry[stream_id]
            name = "split" if stream.kind == "v" else "asplit"
            node = FilterNode(
                name, [stream], [count], {}, output_kinds=(stream.kind,) * count
            )
            branches[stream_id] = list(node.outputs)

        if not branches:
            return outputs

        def take(stream):
            queue = branches.get(id(stream))
            return queue.pop(0) if queue else stream

        # The split nodes are not in `nodes`, so their own inputs stay intact.
        for node in nodes:
            node.inputs = [take(stream) for stream in node.inputs]
        return [take(stream) for stream in outputs]

    @staticmethod
    def _discover_inputs(nodes, outputs):
        """Input files in the order they are first referenced."""
        found = []
        seen = set()
        for stream in [s for node in nodes for s in node.inputs] + list(outputs):
            source = stream.source
            if isinstance(source, InputFile) and id(source) not in seen:
                seen.add(id(source))
                found.append(source)
        return found

    def build(self, outputs):
        """Serialise to ``(input_args, filter_complex, map_labels)``.

        `outputs` are the streams to map, in order.
        """
        outputs = list(outputs)
        nodes = self._collect(outputs)
        outputs = self._dedupe_inputs(nodes, outputs)
        nodes = self._collect(outputs)
        outputs = self._insert_splits(nodes, outputs)
        nodes = self._collect(outputs)

        inputs = self._discover_inputs(nodes, outputs)
        input_index = {id(f): position for position, f in enumerate(inputs)}

        # Name every filter output before emitting, so a chain can reference a
        # label produced later in the list.
        labels = {}
        counter = 0
        for node in nodes:
            for stream in node.outputs:
                labels[id(stream)] = "s%d" % counter
                counter += 1

        def label_of(stream):
            if isinstance(stream.source, InputFile):
                return "%d:%s" % (input_index[id(stream.source)], stream.kind)
            return labels[id(stream)]

        chains = []
        for node in nodes:
            head = "".join("[%s]" % label_of(s) for s in node.inputs)
            tail = "".join("[%s]" % labels[id(s)] for s in node.outputs)
            chains.append(head + node.describe() + tail)

        input_args = []
        for input_file in inputs:
            input_args.extend(input_file.args())

        return input_args, ";".join(chains), [label_of(s) for s in outputs]
