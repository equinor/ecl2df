#!/usr/bin/env python
"""
Extract GRUPTREE information from an Eclipse deck

"""

import sys
import logging
import datetime
import collections

import treelib
import pandas as pd

from .eclfiles import EclFiles
from .common import (
    parse_opmio_date_rec,
    parse_opmio_deckrecord,
    parse_opmio_tstep_rec,
    write_dframe_stdout_file,
)

logger = logging.getLogger(__name__)


def df(deck, startdate=None, welspecs=True):
    """Extract all group information from a deck
    and present as a Pandas Dataframe of all edges.

    Numerical properties for nodes given in GRUPNET will
    be added as extra columns.

    From WELSPECS, well names are extracted and added
    as nodes with an edge to its group.

    The gruptree is a time dependent property,
    with accumulative effects from new occurences of
    GRUPTREE or WELSPECS.

    Whenever the tree changes, the previous tree is copied
    and a new complete tree is added to the dataframe tagged
    with the new date.

    startdate is only relevant when START is not in the deck.

    Args:
        deck: opm.io Deck object or EclFiles

    Returns:
        pd.DataFrame with one row pr edge. Empty dataframe if no
        information is found in deck.
    """

    if startdate is not None:
        date = startdate
    else:
        date = None

    if isinstance(deck, EclFiles):
        deck = deck.get_ecldeck()

    gruptreerecords = []  # list of dict of rows containing an edge.
    grupnetrecords = []

    # In order for the GRUPTREE keywords to accumulate, we
    # store the edges as a dictionary indexed by the edge
    # (which is a tuple of child and parent).
    # The value of the dictionary is GRUPTREE or WELSPECS
    currentedges = dict()

    grupnet_df = pd.DataFrame()

    found_gruptree = False  # Flags which will tell when a new GRUPTREE or
    found_welspecs = False  # WELSPECS have been encountered.
    found_grupnet = False  # GRUPNET has been encountered
    for kword in deck:
        if kword.name == "DATES" or kword.name == "START" or kword.name == "TSTEP":
            # Whenever we encounter a new DATES, it means that
            # we have processed all the GRUPTREE and WELSPECS that
            # have occured since the last date, so this is the chance
            # to dump the parsed data. Also we dump the *entire* tree
            # at every date with a change, not only the newfound edges.
            if currentedges and (found_gruptree or found_welspecs or found_grupnet):
                if date is None:
                    logger.warning("No date parsed, maybe you should pass --startdate")
                    logger.warning("Using 1900-01-01")
                    date = datetime.date(year=1900, month=1, day=1)
                gruptreerecords += _currentedges_to_gruptreerecords(
                    currentedges, grupnet_df, date
                )
                found_gruptree = False
                found_welspecs = False
                found_grupnet = False
            # Done dumping the data for the previous date, parse the fresh
            # date:
            if kword.name == "DATES" or kword.name == "START":
                for rec in kword:
                    date = parse_opmio_date_rec(rec)
                    logging.info("Parsing at date %s", str(date))
            elif kword.name == "TSTEP":
                for rec in kword:
                    steplist = parse_opmio_tstep_rec(rec)
                    # Assuming not LAB units, then the unit is days.
                    days = sum(steplist)
                    if days <= 0:
                        logger.critical("Invalid TSTEP, summed to %s days", str(days))
                        return pd.DataFrame()
                    date += datetime.timedelta(days=days)
                    logger.info(
                        "Advancing %s days to %s through TSTEP", str(days), str(date)
                    )
            else:
                logger.critical("BUG: Should not get here")
                return pd.DataFrame()
        if kword.name == "GRUPTREE":
            found_gruptree = True
            for edgerec in kword:
                edge_dict = parse_opmio_deckrecord(edgerec, "GRUPTREE")
                currentedges[
                    (edge_dict["CHILD_GROUP"], edge_dict["PARENT_GROUP"])
                ] = "GRUPTREE"
        if kword.name == "WELSPECS" and welspecs:
            found_welspecs = True
            for wellrec in kword:
                wspc_dict = parse_opmio_deckrecord(wellrec, "WELSPECS")
                currentedges[(wspc_dict["WELL"], wspc_dict["GROUP"])] = "WELSPECS"
        if kword.name == "GRUPNET":
            found_grupnet = True
            for rec in kword:
                grupnet_data = parse_opmio_deckrecord(rec, "GRUPNET")
                grupnetrecords.append(grupnet_data)
            grupnet_df = (
                pd.DataFrame(grupnetrecords)
                .drop_duplicates(subset="NAME", keep="last")
                .set_index("NAME")
            )

    # Ensure we also store any tree information found after the last DATE statement
    if found_gruptree or found_welspecs:
        gruptreerecords += _currentedges_to_gruptreerecords(
            currentedges, grupnet_df, date
        )
    dframe = pd.DataFrame(gruptreerecords)
    if "DATE" in dframe:
        dframe["DATE"] = pd.to_datetime(dframe["DATE"])
    print(dframe)
    return dframe


def _currentedges_to_gruptreerecords(currentedges, grupnet_df, date):
    """Merge a list of edges with information from the GRUPNET dataframe.

    Edges where there is no parent (root nodes) are identified and added
    as special cases.

    Args:
        currentedges (list): List of tuples with (child, parent)
        grupnet_df (pd.DataFrame): Containing data for each node to add.
        date (datetime.date): Relevant date.

    Returns:
        list: list of dictionaries (that can be made into a dataframe)
    """
    gruptreerecords = []
    childs = set()
    parents = set()
    for edgename, value in currentedges.items():
        rec_dict = {
            "DATE": date,
            "CHILD": edgename[0],
            "PARENT": edgename[1],
            "KEYWORD": value,
        }
        childs |= {edgename[0]}
        parents |= {edgename[1]}
        if edgename[0] in grupnet_df.index:
            rec_dict.update(grupnet_df.loc[edgename[0]])
        gruptreerecords.append(rec_dict)
    roots = parents - childs
    rootrecords = []
    for root in roots:
        rec_dict = {"DATE": date, "CHILD": root, "KEYWORD": "GRUPTREE"}
        if root in grupnet_df.index:
            rec_dict.update(grupnet_df.loc[root])
        rootrecords.append(rec_dict)
    return rootrecords + gruptreerecords


def edge_dataframe2dict(dframe):
    """Convert list of edges in a dataframe into a
    nested dictionary (tree).

    The dataframe cannot have multiple DATEs, filter to the date you
    want prior to calling this function.

    Example::

      [{
        'FIELD': {'OP': {'OP_1': {},
        'OP_2': {},
        'OP_3': {},
        'OP_4': {},
        'OP_5': {}},
        'WI': {'WI_1': {}, 'WI_2': {}, 'WI_3': {}}}
      }]

    Leaf nodes have empty dictionaries as their value.

    Returns:
        list of nested dictionaries, as we in general
            may have more than one root.
    """
    if dframe.empty:
        return [{}]
    if "DATE" in dframe:
        if len(dframe["DATE"].unique()) > 1:
            raise ValueError("Can only handle one date at a time")
    subtrees = collections.defaultdict(dict)
    edges = []  # List of tuples
    for _, row in dframe.iterrows():
        if not pd.isnull(row["PARENT"]):
            edges.append((row["CHILD"], row["PARENT"]))
    for child, parent in edges:
        subtrees[parent][child] = subtrees[child]

    children, parents = zip(*edges)
    roots = set(parents).difference(children)
    trees = []
    for root in list(roots):
        trees.append({root: subtrees[root] for root in roots})
    return trees


def dict2treelib(name, nested_dict):
    """Convert a nested dictonary to a treelib Tree
    object. This function is recursive.

    The treelib representation of the trees is used
    for pretty-printing (in ASCII) of the tree, you
    only need to do str() of the returned result
    from this function.

    See `https://treelib.readthedocs.io/`

    Args:
        name: name of root node
        nested_dict: nested dictonary of the children at the root.

    Return:
        treelib.Tree
    """
    tree = treelib.Tree()
    tree.create_node(name, name)
    for child in nested_dict.keys():
        tree.paste(name, dict2treelib(child, nested_dict[child]))
    return tree


def fill_parser(parser):
    """Set up sys.argv parsers.

    Arguments:
        parser (argparse.ArgumentParser or argparse.subparser): parser
            to fill with arguments
    """
    parser.add_argument("DATAFILE", help="Name of Eclipse DATA file.")
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="Name of output csv file. No CSV dump if empty",
        default="",
    )
    parser.add_argument(
        "-p",
        "--prettyprint",
        #      type=bool,
        action="store_true",
        help="Pretty-print the tree structure",
    )
    parser.add_argument(
        "--startdate",
        type=str,
        help="First schedule date if not defined in input file, YYYY-MM-DD",
        default=None,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Be verbose")
    return parser


def gruptree_main(args):
    """Entry-point for module, for command line utility"""
    if args.verbose:
        logging.basicConfig(level=logging.INFO)
    if not args.output and not args.prettyprint:
        print("Nothing to do. Set --output or --prettyprint")
        sys.exit(0)
    eclfiles = EclFiles(args.DATAFILE)
    dframe = df(eclfiles.get_ecldeck(), startdate=args.startdate)
    if args.prettyprint:
        if "DATE" in dframe:
            for date in dframe["DATE"].dropna().unique():
                print("Date: " + str(date.astype("M8[D]")))
                trees = edge_dataframe2dict(dframe[dframe["DATE"] == date])
                # Returns list of dicts, one for each root found
                # (typically only one)
                for tree in trees:
                    rootname = list(tree.keys())[0]
                    print(dict2treelib(rootname, tree[rootname]))
                print("")
        else:
            logger.warning("No tree data to prettyprint")
    if dframe.empty:
        logger.error("Empty GRUPTREE dataframe, not written to disk!")
    elif args.output:
        write_dframe_stdout_file(dframe, args.output, index=False, caller_logger=logger)
