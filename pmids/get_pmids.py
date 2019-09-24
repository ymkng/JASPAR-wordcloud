#!/usr/bin/env python

import argparse
import coreapi
import gzip
import json
from multiprocessing import Pool
import os
import pickle
import re
import ssl
import subprocess
from tqdm import tqdm
from urllib import parse, request

#-------------#
# Functions   #
#-------------#

def parse_args():
    """
    This function parses arguments provided via the command line and returns an {argparse} object.
    """

    parser = argparse.ArgumentParser()

    parser.add_argument("-d", "--devel", action="store_true", help="development mode (uses hfaistos; default = False)")
    parser.add_argument("-o", default="./", help="output directory (default = ./)", metavar="DIR")
    parser.add_argument("--threads", type=int, default=1, help="threads to use (default = 1)", metavar="INT")

    return(parser.parse_args())

def main():

    # Parse arguments
    args = parse_args()

    # Make files
    get_pmids(args.devel, args.o, args.threads)

def get_pmids(devel=False, output_dir="./", threads=1):

    # Initialize
    taxons = ["fungi", "insects", "nematodes", "plants", "vertebrates"]

    # Globals
    global client
    global codec
    global cwd
    client = coreapi.Client()
    codec = coreapi.codecs.CoreJSONCodec()
    cwd = os.getcwd()

    # Create output directory
    if not os.path.exists(os.path.abspath(output_dir)):
        os.makedirs(os.path.abspath(output_dir))

    # Get JASPAR URL
    jaspar_url = "http://jaspar.genereg.net/"
    if devel:
        jaspar_url = "http://hfaistos.uio.no:8002/"

    # Get uniaccs
    uniaccs = set()
    for taxon in taxons:
        uniaccs.update(_get_taxon_uniaccs(taxon, output_dir, jaspar_url))

    # Get uniacc to entrezid mappings
    uniacc2entrezid = _get_uniacc_to_entrezid_mappings(uniaccs, output_dir)

    # Get entrezids
    entrezids = set(uniacc2entrezid.values())

    # Get entrezid to pmid mappings
    entrezid2pmid = _get_entrezid_to_pmid_mappings(entrezids, output_dir)

    ##
    ## This should be encapsulated in a function
    ##


    # For each taxon...
    for taxon in taxons:

        # Initialize
        pmids = set()

        # Get uniaccs
        uniaccs = _get_taxon_uniaccs(taxon, output_dir, jaspar_url)

        # For each uniacc...
        for uniacc in uniaccs:

            # Skip if uniacc not mapped to an entrezid
            if uniacc not in uniacc2entrezid:
                continue

            # Skip if entrezid not mapped to a pmid
            if uniacc2entrezid[uniacc] not in entrezid2pmid:
                continue

            # For each pmid
            for pmid in entrezid2pmid[uniacc2entrezid[uniacc]]:
                pmids.add(pmid)

        # Skip if taxon directory already exists
        taxon_dir = os.path.join(os.path.abspath(output_dir), taxon)
        if not os.path.exists(taxon_dir):

            # Create taxon directory
            os.makedirs(taxon_dir)

            # Move to taxon directory
            os.chdir(taxon_dir)

            # Parallelize
            pool = Pool(threads)
            for _ in tqdm(pool.imap(_get_pmid, pmids), desc=taxon, total=len(pmids)):
                pass
            pool.close()
            pool.join()

            # Return to original directory
            os.chdir(cwd)

def _get_taxon_uniaccs(taxon, output_dir="./", jaspar_url="http://jaspar.genereg.net/"):

    # Move to taxon directory
    os.chdir(output_dir)

    # Skip if pickle file already exists
    pickle_file = ".uniaccs.%s.pickle" % taxon
    if not os.path.exists(pickle_file):

        # Initialize
        uniaccs = set()
        taxon_url = os.path.join(jaspar_url, "api", "v1", "taxon", taxon)
        taxon_response = client.get(taxon_url)
        taxon_json_obj = json.loads(codec.encode(taxon_response))

        # While there are more pages...
        while taxon_json_obj["next"] is not None:

            # For each uniacc...
            for uniacc in _get_results_uniacc(taxon_json_obj["results"], jaspar_url):

                # Add uniacc
                uniaccs.add(uniacc)

            # Go to next page
            taxon_response = client.get(taxon_json_obj["next"])
            taxon_json_obj = json.loads(codec.encode(taxon_response))

        # Do last page
        for uniacc in _get_results_uniacc(taxon_json_obj["results"], jaspar_url):

            # Add uniacc
            uniaccs.add(uniacc)

        # Write pickle file
        with open(pickle_file, "wb") as f:
            pickle.dump(uniaccs, f)

    # Load pickle file
    with open(pickle_file, "rb") as f:
        uniaccs = pickle.load(f)

    # Return to original directory
    os.chdir(cwd)

    return(uniaccs)

def _get_results_uniacc(results, jaspar_url="http://jaspar.genereg.net/"):

    # For each profile...
    for profile in results:

        # If profile from CORE collection...
        if profile["collection"] == "CORE":

            # Fix bugged cases
            if profile["matrix_id"] == "MA0328.1":
                return("P0CY08")
            if profile["matrix_id"] == "MA0110.1":
                return("P46667")
            if profile["matrix_id"] == "MA0058.1":
                return("P61244")
            if profile["matrix_id"] == "MA0046.1":
                return("P20823")
            if profile["matrix_id"] == "MA0098.1":
                return("P14921")
            if profile["matrix_id"] == "MA0052.1":
                return("Q02078")
            if profile["matrix_id"] == "MA0024.1":
                return("Q01094")
            if profile["matrix_id"] == "MA0138.1":
                return("Q13127")
    
            # Initialize
            profile_url = os.path.join(jaspar_url, "api", "v1", "matrix", profile["matrix_id"])
            profile_response = client.get(profile_url)
            profile_json_obj = json.loads(codec.encode(profile_response))

            # For each uniprot...
            for uniacc in profile_json_obj["uniprot_ids"]:

                yield(uniacc)

def _get_uniacc_to_entrezid_mappings(uniaccs, output_dir="./"):

    # Initialize
    uniacc2entrezid = {}
    gcontext = ssl.SSLContext()
    url = "https://www.uniprot.org/uploadlists/"

    # Move to taxon directory
    os.chdir(output_dir)

    # Skip if pickle file already exists
    pickle_file = ".uniacc2entrezid.pickle"
    if not os.path.exists(pickle_file):

        # Set query
        query = {
            "from": "ACC+ID",
            "to": "P_ENTREZGENEID",
            "format": "tab",
            "query": " ".join(list(uniaccs))
        }

        # Encode parameters
        params = parse.urlencode(query=query).encode("utf-8")

        # Make request
        req = request.Request(url, params)

        # Get response
        with request.urlopen(req, context=gcontext) as f:
            response = f.read().decode("utf-8")

        # For each line...
        for line in response.split("\n"):

            # i.e. a mapping
            m = re.search("(\w+)\t(\d+)", line)
            if m:

                # Map uniacc to entrezid
                uniacc, entrezid = line.split("\t")
                uniacc2entrezid.setdefault(uniacc, entrezid)

        # Write pickle file
        with open(pickle_file, "wb") as f:
            pickle.dump(uniacc2entrezid, f)

    # Load pickle file
    with open(pickle_file, "rb") as f:
        uniacc2entrezid = pickle.load(f)

    # Return to original directory
    os.chdir(cwd)

    return(uniacc2entrezid)

def _get_entrezid_to_pmid_mappings(entrezids, output_dir="./"):

    # Initialize
    entrezid2pmid = {}
    url = "ftp://ftp.ncbi.nlm.nih.gov/gene/DATA/"
    file_name = "gene2pubmed.gz"

    # Move to taxon directory
    os.chdir(output_dir)

    # Skip if pickle file already exists
    pickle_file = ".entrezid2pmid.pickle"
    if not os.path.exists(pickle_file):

        # Download gene to pubmed mappings
        request.urlretrieve(os.path.join(url, file_name), file_name)

        with gzip.open(file_name, "r") as f:

            # For each line
            for line in f:

                # Get taxid, entrezid, pmid
                taxid, entrezid, pmid = line.decode("utf-8").split("\t")

                # If valid entrezid...
                if entrezid in entrezids:

                    # Map entrezid to pmid
                    entrezid2pmid.setdefault(entrezid, [])
                    entrezid2pmid[entrezid].append(pmid.strip("\n"))

        if os.path.exists(file_name):
            os.remove(file_name)

        # Write pickle file
        with open(pickle_file, "wb") as f:
            pickle.dump(entrezid2pmid, f)

    # Load pickle file
    with open(pickle_file, "rb") as f:
        entrezid2pmid = pickle.load(f)

    # Return to original directory
    os.chdir(cwd)

    return(entrezid2pmid)

def _get_pmid(pmid):

    # Skip if already downloaded
    rds_file = "%s.rds" % pmid
    if not os.path.exists(rds_file):

        # Get pmid
        cmd = "Rscript ../get_pmid.R %s" % pmid
        process = subprocess.run([cmd], shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

#-------------#
# Main        #
#-------------#

if __name__ == "__main__":

    main()