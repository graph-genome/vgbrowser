from typing import List, Iterable, Set
from django.db import models
from django.db.models import QuerySet

from Utils.models import CustomSaveManager


# GraphGenome specific error classes for more informative error catching
class NoAnchorError(ValueError):
    pass
class PathOverlapError(ValueError):
    pass
class NoOverlapError(PathOverlapError):
    pass
class NodeMissingError(ValueError):
    pass


class ZoomLevel(models.Model):
    """Each Graph Genome has multiple Zoom levels.  A zoom level is a collection of Paths
    at that zoom level.  When a Path is unmodified from a summarization step, it can reuse
    a previous Path without duplicating it.
    Zoom was originally a single integer in Path, but it turned out a helper class for queries
    would result in more robust code and allow Path reuse."""
    graph = models.ForeignKey('GraphGenome', on_delete=models.CASCADE)
    zoom = models.IntegerField(default=0)  # Zoom level starts at 0 for nucleotide level and moves up
    # Nodes can be shared between zoom levels.
    paths = models.ManyToManyField('Path', related_name='zoom_levels', blank=True,
                                         help_text='One path for each accession in a zoom level.  '
                                                   'Paths can be reused unmodified in multiple levels.')

    class Meta:
        unique_together = ['graph', 'zoom']  # one ZoomLevel at each integer


class GraphManager(models.Manager):
    """custom create() methods for consistency and convenience"""
    def create(self, **kwargs):
        """When a graph is created.  It should also automatically create the first ZoomLevel."""
        graph = super(GraphManager, self).create(**kwargs)
        ZoomLevel.objects.create(graph=graph, zoom=0)  # sequence_level created
        return graph


class GraphGenome(models.Model):
    """Graph Genomes in general are defined as a collection of unordered Nodes which contain sequence,
    and one Path per individual.  A Path visits Nodes in any order, on either strand.  This directed
    graph then contains the relationships of all individuals to each other through shared sequence.
    GraphGenomes in this database contain an extra concept not found in GFA or VG: Summarization.
    GraphGenome has multiple ZoomLevels which each contain a full set of Paths.  Paths at higher
    zoom levels are shorter and visit summary nodes that explain or discard trivial variation to
    allow user to focus on larger graph structure."""
    name = models.CharField(max_length=1000)
    # Zoom level is defined in Paths only.
    # Nodes can be shared between zoom levels.

    objects = GraphManager()

    @property
    def sequence_level(self) -> 'ZoomLevel':
        return self.zoomlevel_set.filter(zoom=0).first()

    @property
    def paths(self) -> QuerySet:
        """Getter only.  Shortcut for DB."""
        return self.sequence_level.paths

    @property
    def nodes(self):
        """Getter only.  Shortcut for DB."""
        return self.node_set.all()

    def __repr__(self):
        """Warning: the representation strings are very sensitive to whitespace"""
        return f"Graph: {self.name}\n{self.paths.count()} paths  {self.node_set.count()} nodes."

    def __eq__(self, other):
        if isinstance(other, GraphGenome):
            return other.node_set.count() == self.node_set.count() and \
                   other.paths.count() == self.paths.count()  # other.name == self.name and \
        return False

    @classmethod
    def load_from_xg(cls, file: str, xg_bin: str) -> 'GraphGenome':
        """XG is a graph format used by VG (variation graph).  This method builds a
        database GraphGenome to exactly mirror the contents of an XG file."""
        from Graph.gfa import GFA
        gfa = GFA.load_from_xg(file, xg_bin)
        return gfa.to_graph()

    def save_as_xg(self, file: str, xg_bin: str):
        """XG is a graph format used by VG (variation graph).  This method exports
        a database GraphGenome as an XG file."""
        from Graph.gfa import GFA
        gfa = GFA.from_graph(self)
        gfa.save_as_xg(file, xg_bin)

    def append_node_to_path(self, node_id, strand, path_name) -> None:
        """This is the preferred way to build a graph in a truly non-linear way.
        Nodes will be created if necessary.
        NodeTraversal is appended to Path (order dependent) and PathIndex is added to Node
        (order independent)."""
        if node_id not in self.nodes:  # hasn't been created yet, need to retrieve from dictionary of guid
            if isinstance(node_id, str):
                self.nodes[node_id] = Node('', [], node_id)
            else:
                raise ValueError("Provide the id of the node, not", node_id)
        Path.objects.get(name=path_name).append_node(Node.objects.get(name=node_id), strand)

    def node(self, node_name):
        return Node.objects.get(name=node_name, graph=self)



class Node(models.Model):
    """Nodes are the fundamental content carriers for sequence.  A Path
    can traverse nodes in any order, on both + and - strands.
    Nodes can be utilized by Paths in multiple zoom levels.
    Only first order Nodes (zoom=0) have sequences, but all have names which can be used
    to fetch the node from GraphGenome.node()."""
    seq = models.CharField(max_length=255, blank=True)
    name = models.CharField(max_length=15)
    graph = models.ForeignKey(GraphGenome, on_delete=models.CASCADE)
    summarized_by = models.ForeignKey('Node', null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name='children')
    # Break points for haploblocks - Erik Garrison - service for coordinates
    # Start and stop positions for a node

    class Meta:
        unique_together = ['graph', 'name']

    def __len__(self):
        return self.nodetraversal_set.count()

    # def __repr__(self):
    #     """Paths representation is sorted because set ordering is not guaranteed."""
    #     return repr(self.seq) + \
    #     ', {' + ', '.join(str(i) for i in list(self.paths)) + '}'

    # def __eq__(self, other):
    #     if not isinstance(other, Node):
    #         print("Warn: comparing Node and ", type(other), other)
    #         return False
    #     return self.seq == other.seq and self.paths == other.paths  # and self.id == other.id

    def __hash__(self):
        return (hash(self.seq) + 1) * hash(self.name)

    def to_gfa(self, segment_id: int):
        return '\t'.join(['S', str(segment_id), self.seq])

    def specimens(self, zoom_level) -> Set[int]:
        return self.nodetraversal_set.filter(path_zoom=zoom_level).value_list('path_id', flat=True)

    def upstream(self, zoom_level) -> Set[int]:
        traverses = self.nodetraversal_set.filter(path_zoom=zoom_level)  #.value_list('node_id', flat=True)
        # Node.objects.filter(id__in=traverses).values_list('id', flat=True)
        return set(t.upstream_id() for t in traverses)

    def downstream(self, zoom_level) -> Set[int]:
        traverses = self.nodetraversal_set.filter(path_zoom=zoom_level).all()
        return set(t.downstream_id() for t in traverses)

    def __repr__(self):
        return "N%s(%s)" % (str(self.name), self.seq)

    def details(self):
        return f"""Node{self.name}: {self.seq}
        upstream: {dict((key, value) for key, value in self.upstream.items())}
        downstream: {dict((key, value) for key, value in self.downstream.items())}
        {len(self.specimens)} specimens: {self.specimens}"""

    def is_nothing(self):
        """Useful in Node class definition to check for Node.NOTHING"""
        return self.name == '-1'

    def validate(self):
        """Returns true if the Node has specimens and does not have any negative
        transition values, raises an AssertionError otherwise."""
        if not self.specimens:
            assert self.specimens, "Specimens are empty" + self.details()
        for node, weight in self.upstream.items():
            if not node.is_nothing() and weight < 0:
                print(self.details())
                assert weight > -1, node.details()

        for node, weight in self.downstream.items():
            if not node.is_nothing() and weight < 0:
                print(self.details())
                assert weight > -1, node.details()
        return True


# Node.NOTHING is essential "Not Applicable" when used to track transition rates between nodes.
# Node.NOTHING is an important concept to Haploblocker, used to track upstream and downstream
# that transitions to an unknown or untracked state.  As neglect_nodes removes minority
# allele nodes, there will be specimens downstream that "come from" Node.NOTHING, meaning their
# full history is no longer tracked.  Node.NOTHING is a regular exception case for missing data,
# the ends of chromosomes, and the gaps between haplotype blocks.
Node.NOTHING = Node(-1)


class PathManager(models.Manager):
    """custom create() methods for consistency and convenience"""
    def create(self, accession, graph, zoom=0):
        """Fetches the appropriate ZoomLevel object and creates a link when the Path is first
        created. """
        path = super(PathManager, self).create(accession=accession)
        ZoomLevel.objects.get_or_create(graph=graph, zoom=zoom)[0].paths.add(path)
        return path


class Path(models.Model):
    """Paths represent the linear order of on particular individual (accession) as its genome
    was sequenced.  A path visits a series of nodes and the ordered concatenation of the node
    sequences is the accession's genome.  Create Paths first from accession names, then append
    them to Nodes to link together."""
    accession = models.CharField(max_length=1000)  # one path per accession
    summarized_by = models.ForeignKey('Path', related_name='summary_child',
                                      blank=True, null=True, on_delete=models.SET_NULL)

    objects = PathManager()

    # class Meta:  we need a database check where each accession name only occurs once per zoom level
    #     unique_together = ['accession', 'zoom_levels']

    def __getitem__(self, path_index):
        return self.nodes.filter(order=path_index)

    def __repr__(self):
        """Warning: the representation strings are very sensitive to whitespace"""
        return "'" + self.accession + "'"

    def __eq__(self, other):
        return self.accession == other.accession

    def __hash__(self):
        return hash(self.accession)

    @property
    def nodes(self) -> QuerySet:
        return NodeTraversal.objects.filter(path=self).order_by('order')

    @property
    def graph(self) -> GraphGenome:
        return self.zoom_levels.first().graph

    def append_gfa_nodes(self, nodes):
        assert hasattr(nodes[0], 'orient') and hasattr(nodes[0], 'name'), 'Expecting gfapy.Gfa.path'
        for node in nodes:
            # TODO: could be sped up with bulk_create after checking and setting order
            NodeTraversal(node=Node.objects.get(name=node.name),
                          path=self, strand=node.orient).save()

    def append_node(self, node: Node, strand: str):
        """This is the preferred way to build a graph in a truly non-linear way.
        NodeTraversal is appended to Path (order dependent) and PathIndex is added to Node (order independent)."""
        NodeTraversal(node=node, path=self, strand=strand).save()

    # @classmethod
    # def build(cls, name: str, seq_of_nodes: List[str]):
    #     node = Node.objects.create(seq)
    #     for p in paths:
    #         NodeTraversal.objects.create(node, path)

    def name(self):
        return self.accession

    def to_gfa(self):
        return '\t'.join(['P', self.accession, "+,".join([x.node.name + x.strand for x in self.nodes]) + "+", ",".join(['*' for x in self.nodes])])


class NodeTraversal(models.Model):
    """Link from a Path to a Node it is currently traversing.  Includes strand"""
    node = models.ForeignKey(Node, db_index=True, on_delete=models.CASCADE)
    path = models.ForeignKey(Path, db_index=True, on_delete=models.CASCADE, help_text='')
    strand = models.CharField(choices=[('+', '+'),('-', '-')], default='+', max_length=1)
    # order is set automatically in the CustomSaveManager
    order = models.IntegerField(help_text='Defines the order a path lists traversals. '
                                          'The scale of order is not preserved between zoom levels.')

    objects = CustomSaveManager()

    def __repr__(self):
        if self.strand == '+':
            return self.node.seq
        else:
            complement = {'A': 'T', 'C': 'G', 'G': 'C', 'T': 'A'}
            return "".join(complement.get(base, base) for base in reversed(self.node.seq))

    def __eq__(self, other):
        return self.node.id == other.node.id and self.strand == other.strand

    def save(self, **kwargs):
        """Checks the largest 'order' value in the current path and increments by 1.
        IMPORTANT NOTE: save() does not get called if you do NodeTraverseal.objects.create
        or get_or_create"""
        if self.order is None:
            last_traversal = self.path.nodetraversal_set.all().order_by('-order').first()
            self.order = 0 if not last_traversal else last_traversal.order + 1
        super(NodeTraversal, self).save(**kwargs)

    def fetch_neighbor(self, target_index):
        query = NodeTraversal.objects.filter \
            (path=self.path, order=target_index).values_list('node__id', flat=True)
        if query:
            return query[0]
        return -1

    def upstream_id(self):
        target_index = self.order - 1
        return self.fetch_neighbor(target_index)

    def downstream_id(self):
        target_index = self.order + 1
        return self.fetch_neighbor(target_index)

    def neighbor(self, target_index):
        try:
            return NodeTraversal.objects.get(path=self.path, order=target_index)
        except NodeTraversal.DoesNotExist:
            return None

    def upstream(self):
        target_index = self.order - 1
        return self.neighbor(target_index)

    def downstream(self):
        target_index = self.order + 1
        return self.neighbor(target_index)

