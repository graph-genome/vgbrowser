"""
HaploBlocker handles Graph Summarization explained in:
Graph Summarization: https://github.com/graph-genome/vgbrowser/issues/3
HaploBlocker: https://github.com/graph-genome/vgbrowser/issues/19
"""
from typing import List
import numpy as np
from collections import defaultdict
from copy import copy
from Graph.models import Node

BLOCK_SIZE = 20
FILTER_THRESHOLD = 4


def first(iterable):
    return next(iter(iterable))

def read_data(file_path):
    """Reads one of Torsten's SNP files.  In the file, Individuals are columns, not rows.
    This method returns both loci (all 501 individual alleles at one loci) and the Transposed
    individuals (all 32,000 loci for one individual at a time)."""
    loci = []
    with open(file_path) as ke:
        for line in ke.readlines():
            loci.append(tuple(int(x) for x in line.split()))

    individuals = np.array(loci).T.tolist()
    return loci, individuals


def signature(individual, start_locus):
    return tuple(individual[start_locus: start_locus + BLOCK_SIZE])


def get_unique_signatures(individuals, start_locus, current_graph):
    """A signature is a series of BLOCK_SIZE SNPs inside of a locus.  We want to know how many
    unique signatures are present inside of one locus.  A Node is created for each unique
    signature found.
    EX: Signature(1000001011013100200)

    This method does not currently have error tolerance like R Haploblocker"""
    unique_blocks = {}
    for individual in individuals:
        sig = signature(individual, start_locus)
        if sig not in unique_blocks:
            unique_blocks[sig] = Node(name=f'{len(unique_blocks)}:{start_locus // BLOCK_SIZE}-{start_locus // BLOCK_SIZE}',
                                      seq=''.join(str(x) for x in sig),
                                      graph=current_graph)
    return unique_blocks


def get_all_signatures(alleles, individuals, current_graph):
    unique_signatures = []
    for locus_start in range(0, len(alleles) - BLOCK_SIZE, BLOCK_SIZE):  # discards remainder
        sig = get_unique_signatures(individuals, locus_start, current_graph)
        unique_signatures.append(sig)
    return unique_signatures


def build_individuals(individuals, unique_signatures):
    """Describes an individual as a list of Nodes that individual visits.
    simplified_individuals is a list of loci which contain a list of Nodes which each contain specimen
    build nodes:  [0] first 4 are the 4 starting signatures in window 0.
    Nodes represent a collection of individuals with the same signature at that locus
    For each node list which individuals are present at that node"""
    simplified_individuals = []
    for i_specimen, specimen in enumerate(individuals):
        my_simplification = []
        for w, window in enumerate(unique_signatures):  # the length of the genome
            sig = signature(specimen, w * BLOCK_SIZE)
            my_simplification.append(unique_signatures[w][sig])
        simplified_individuals.append(my_simplification)
    return simplified_individuals


def populate_transitions(simplified_individuals):
    """
    List transition rates from one node to all other upstream and downstream.
    This method populates Node.specimens and begins the process of side-effecting Nodes.
    To rebuild a fresh Graph copy, you must start at get_all_signatures()
    :param simplified_individuals:
    """
    for i, indiv in enumerate(simplified_individuals):
        # look what variants are present
        for x, node in enumerate(indiv):
            node.specimens.add(i)
            if x + 1 < len(indiv):
                node.downstream[indiv[x + 1]] += 1
            # else:
            #     node.downstream[Node.NOTHING] += 1
            if x - 1 >= 0:
                node.upstream[indiv[x - 1]] += 1
            # else:
            #     node.upstream[Node.NOTHING] += 1


def update_transition(node):
    """Only transition values for nodes already listed in upstream and downstream will be calculated."""
    if not node.is_nothing():
        update_stream_transitions(node, 'upstream')
        update_stream_transitions(node, 'downstream')

    return node


def update_stream_transitions(node, stream):
    """This will updated either upstream or downstream transition counts based on the
    the value of 'stream'.  This is a meta-programming function that requires the exact
    name of the class field 'upstream' or 'downstream' to work."""
    g = getattr  #
    running = g(node, stream).keys()
    setattr(node, stream, defaultdict(lambda: 0))
    for n in running:
        if not n.is_nothing():
            g(node, stream)[n] = len(node.specimens.intersection(n.specimens))
    accounted_upstream = sum(g(node, stream).values()) - g(node, stream)[Node.NOTHING]
    g(node, stream)[Node.NOTHING] = len(node.specimens) - accounted_upstream
    assert all([count > -1 for count in g(node, stream).values()]), node.details()
    # Cleans up old keys including Node.NOTHING
    to_be_deleted = {key for key, count in g(node, stream).items() if count == 0}
    for key in to_be_deleted:
        g(node, stream).pop(key, None)


def simple_merge(full_graph):
    """ Side effects full_graph by merging any consecutive nodes that have
    identical specimens and removing the redundant node from full_graph.
    :param full_graph:
    :return: full_graph modified
    """
    n = 0
    while n < len(full_graph):  # size of global_nodes changes, necessitating this weird loop
        node = full_graph[n]
        if len(node.downstream) == 1:
            next_node = first(node.downstream.keys())
            if len(node.specimens) == len(next_node.specimens):
                # Torsten deletes nodeA and modifies next_node
                next_node.upstream = node.upstream
                next_node.start = node.start
                # prepare to delete node by removing references
                for parent in node.upstream.keys():
                    if not parent.is_nothing():
                        count = parent.downstream[node]
                        del parent.downstream[node]  # updating pointer
                        parent.downstream[next_node] = count
                full_graph.remove(node)  # delete node
                n -= 1
        n += 1
    return full_graph


def delete_node(node, cutoff):
    """Changes references to this node to add to references to Node.NOTHING"""
    if cutoff < 1:
        return  # if cutoff is 0, then don't touch upstream and downstream
    for parent, count in node.upstream.items():
        parent.downstream[Node.NOTHING] += parent.downstream[node]
        del parent.downstream[node]
    for descendant, count in node.downstream.items():
        descendant.upstream[Node.NOTHING] += descendant.upstream[node]
        del descendant.upstream[node]


def neglect_nodes(all_nodes, deletion_cutoff=FILTER_THRESHOLD):
    """Deletes nodes if they have too few specimens supporting them defined by
    :param deletion_cutoff
    :returns a new list of nodes lacking the pruned nodes in all_nodes"""
    nodes_to_delete = set()
    for node in all_nodes:
        if len(node.specimens) <= deletion_cutoff:
            delete_node(node, deletion_cutoff)  # TODO: check if this will orphan
            nodes_to_delete.add(node)
    filtered_nodes = [x for x in all_nodes if x not in nodes_to_delete]
    # TODO: remove orphaned haplotypes in a node that transition to and from zero within a 10 window length
    return filtered_nodes


def split_one_group(prev_node, anchor, next_node):
    """ Called when up.specimens == down.specimens
    Comment: That is actually the case we want to split up to obtain longer blocks later
    Extension of full windows will take care of potential loss of information later"""
    my_specimens = copy(anchor.specimens)  # important to copy or side effects occur
    if not prev_node.is_nothing():  # normal case
        my_specimens = my_specimens.intersection(prev_node.specimens)
    if not next_node.is_nothing():  # normal case
        my_specimens = my_specimens.intersection(next_node.specimens)
    if prev_node.is_nothing() and next_node.is_nothing():  # exceptional: both are nothing node
        my_specimens = copy(anchor.specimens)
        # removing all specimens that transition to nothing
        for n in anchor.downstream.keys():
            if n.is_nothing():  # remove dead leads
                my_specimens -= n.specimens
        for n in anchor.upstream.keys():
            if n.is_nothing():  # remove dead leads
                my_specimens -= n.specimens

    my_upstream, my_downstream = copy(prev_node.upstream), copy(next_node.downstream)
    if prev_node.is_nothing():  # Rare case
        my_upstream = copy(anchor.upstream)
    if next_node.is_nothing():  # Rare case
        my_downstream = copy(anchor.downstream)

    # TODO: what about case where more content is joining downstream?
    new = Node(777, my_specimens, my_upstream, my_downstream)

    # Update Specimens in prev_node, anchor, next_node
    anchor.specimens -= new.specimens
    prev_node.specimens -= new.specimens
    next_node.specimens -= new.specimens

    # Update upstream/downstream
    update_neighbor_pointers(new)
    suspects = {new, prev_node, anchor, next_node}.union(set(new.upstream.keys()), set(new.downstream.keys()))
    for n in suspects:
        update_transition(n)
    new.validate()
    return new


def update_neighbor_pointers(new_node):
    """Ensure that my new upstream pointers have matching downstream pointers in neighbors,
    and vice versa.  This does not set correct transition rates, it only makes the nodes connected."""
    for n in new_node.upstream.keys():
        if not n.is_nothing():
            n.downstream[new_node] = 1
    for n in new_node.downstream.keys():
        if not n.is_nothing():
            n.upstream[new_node] = 1


def split_groups(all_nodes: List[Node]):
    """When two haplotypes have a locus in common with no variation, then the graph represents
    this with a single anchor node flanked by 2 haplotypes on either side.  This means 5 Nodes
    are present where 2 would suffice.  Split groups splits the anchor node and gives pieces
    to each haplotype; reducing 5 Nodes to 2.

    :Returns new graph with less nodes, all_nodes is still modified, but length doesn't change
    Note: This is called crossmerge in the R code.
    TODO: Ideally, the database would retain some record of how many nucleotides are shared between
    the two new haplotype nodes."""
    new_graph = list(all_nodes)
    for node in all_nodes:
        # check if all transition upstream match with one of my downstream nodes
        if len(node.specimens) > 0:
            # Matchup upstream and downstream with specimen identities
            for up in tuple(node.upstream.keys()):
                for down in tuple(node.downstream.keys()):
                    set1 = copy(up.specimens)
                    set2 = copy(down.specimens)
                    if up.is_nothing():
                        set1 = copy(node.specimens)
                        for index in tuple(node.upstream.keys()):
                            if not index.is_nothing():
                                set1.difference_update(index.specimens)
                    if down.is_nothing():
                        set2 = copy(node.specimens)
                        for index in tuple(node.downstream.keys()):
                            if not index.is_nothing():
                                set2.difference_update(index.specimens)

                    if set1 == set2 and len(set1) > 0:
                        new_node = split_one_group(up, node, down)
                        new_graph.append(new_node)

    filtered = neglect_nodes(new_graph, 0)  # Delete nodes with zero specimens from the Graph?
    return filtered
