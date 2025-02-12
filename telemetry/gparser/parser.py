from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from tqdm import tqdm
from urllib.parse import urlparse
import json
import os
import re
import requests
import traceback
import junitparser
from junitparser import JUnitXml, Skipped, Error, Failure

# Temporary directory to contain downloaded file
FILE_DIR = 'event_horizon'

def get_parser(url):
    '''Factory method that provides appropriate parser object base on given url'''
    parsers = {
        '^.*dmesg_[a-zA-Z0-9-]+\.log': Dmesg,
        '^.*dmesg_.+err\.log': DmesgError,
        '^.*dmesg_.+warn\.log': DmesgWarning,
        '^.*enumerated_devs\.log': EnumeratedDevs,
        '^.*missing_devs\.log': MissingDevs,
        '^.*pyadi-iio.*\.xml': [PytestFailure, PytestSkipped, PytestError],
        '^.*HWTestResults\.xml': [MatlabFailure, MatlabSkipped, MatlabError]
    }

    # find parser
    for sk, parser in parsers.items():
        if re.match(sk, url):
            if isinstance(parser, list):
                return [p(url) for p in parser]
            return parser(url)

    raise Exception("Cannot find Parser for {}".format(url))

def retry_session(retries=3, backoff_factor=0.3, 
        status_forcelist=(429, 500, 502, 504),
        session=None,
    ):
        session = session or requests.Session()
        retry = Retry(
            total=retries,
            read=retries,
            connect=retries,
            backoff_factor=backoff_factor,
            status_forcelist=status_forcelist,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        return session

def grabber(url, fname):
    ''' Downloads file from a given url as fname'''
    resp = retry_session().get(url, stream=True)
    if not resp.ok:
        raise Exception(url + " - url not found!" )
    total = int(resp.headers.get("content-length", 0))
    with open(fname, "wb") as file, tqdm(
        desc=fname, total=total, unit="iB", unit_scale=True, unit_divisor=1024,
    ) as bar:
        for data in resp.iter_content(chunk_size=1024):
            size = file.write(data)
            bar.update(size)

def remove_suffix(input_string, suffix):
    if suffix and input_string.endswith(suffix):
        return input_string[:-len(suffix)]
    return input_string


class Parser:
    '''Base class for a parser object'''
    def __init__(self, url):
        
        self.url = url
        self.server = None
        self.job = None
        self.job_no = None
        self.job_date = None
        self.file_name = None
        self.file_info = None
        self.target_board = None
        self.artifact_info_type = None
        self.payload_raw = []
        self.payload = []
        self.payload_param = []
        self.initialize()

    def initialize(self):
        url_ = urlparse(self.url)
        self.multilevel = False
        if len(url_.path.split('/job/')) > 2:
            self.multilevel = True

        # initialize attributes
        self.server = url_.scheme + '://' + url_.netloc + '/' + url_.path.split('/')[1]
        self.job, self.job_no, self.job_date  = self.get_job_info()
        file_info = self.get_file_info()
        self.file_name = file_info[0]
        self.file_info = file_info[1]
        self.target_board = file_info[2]
        self.artifact_info_type=file_info[3]
        self.payload_raw=self.get_payload_raw()
        payload_parsed=self.get_payload_parsed()
        if isinstance(self, xmlParser):
            self.payload=payload_parsed[0]
            self.payload_param=payload_parsed[1]
        else:
            self.payload=payload_parsed
            for k in range(len(payload_parsed)):
                self.payload_param.append("NA") 

    def show_info(self):
        return self.__dict__

    def get_job_info(self):
        '''returns jenkins project name, job no and job date'''
        if self.multilevel:
            url = urlparse(self.url)
            job=url.path.split('/')[3] + '/' + url.path.split('/')[5]
            job_no=url.path.split('/')[6]
            # TODO: get job date using jenkins api
            job_date=None
            return (job,job_no,job_date)

        raise Exception("Does not support non multilevel yet!")

    def get_file_info(self):
        '''returns file name, file info, target_board, artifact_info_type'''
        if self.multilevel:
            url = urlparse(self.url)
            file_name = url.path.split('/')[-1]
            file_info = file_name.split('_')
            target_board=file_info[0]
            artifact_info_type=file_info[1] + '_' + file_info[2]
            artifact_info_type = remove_suffix(artifact_info_type,".log")
            return (file_name, file_info, target_board, artifact_info_type)

        raise Exception("Does not support non multilevel yet!")


    def get_payload_raw(self):
        payload = []
        file_path = os.path.join(FILE_DIR, self.file_name)
        try:
            if not os.path.exists(FILE_DIR):
                os.mkdir(FILE_DIR)
            grabber(self.url, file_path)
            with open(file_path, "r") as f:
                payload = [l.strip() for l in f.readlines()]
        except Exception as ex:
            traceback.print_exc()
            print("Error Parsing File!")
        finally:
            os.remove(file_path)
        return payload

    def get_payload_parsed(self):
        payload = []
        for p in self.payload_raw:
            # try to extract timestamp from data
            x = re.search("\[.*(\d+\.\d*)\]\s(.*)", p)
            if x:
                payload.append((x.group(1),x.group(2)))
            else:
                x = re.search("(.*)", p)
                payload.append(x.group(1))
        return payload

class Dmesg(Parser):

    def __init__(self, url):
        super(Dmesg, self).__init__(url)

    def get_file_info(self):
        '''returns file name, file info, target_board, artifact_info_type'''
        if self.multilevel:
            url = urlparse(self.url)
            file_name = url.path.split('/')[-1]
            file_info = file_name.split('_')
            target_board=file_info[1]
            artifact_info_type=file_info[0]
            if len(file_info) == 3:
                artifact_info_type += '_' + file_info[2]
            artifact_info_type = remove_suffix(artifact_info_type,".log")
            return (file_name, file_info, target_board, artifact_info_type)

        raise Exception("Does not support non multilevel yet!")

class DmesgError(Dmesg):
    
    def __init__(self, url):
        super(DmesgError, self).__init__(url)

class DmesgWarning(Dmesg):
    
    def __init__(self, url):
        super(DmesgWarning, self).__init__(url)

class EnumeratedDevs(Parser):
    
    def __init__(self, url):
        super(EnumeratedDevs, self).__init__(url)

class MissingDevs(Parser):
    
    def __init__(self, url):
        super(MissingDevs, self).__init__(url)

class xmlParser(Parser):
    def __init__(self, url):
        super(xmlParser, self).__init__(url)
        
    def get_file_info(self):
        '''returns file name, file info, target_board, artifact_info_type'''
        if self.multilevel:
            url = urlparse(self.url)
            url_path = url.path.split('/')
            file_name = url_path[-1]
            parser_type = type(self).__name__
            x = [i for i, c in enumerate(parser_type) if c.isupper()]
            file_info = (parser_type[:x[1]]+'_'+parser_type[x[1]:]).lower()
            target_board = file_name.replace('_','-')
            target_board = remove_suffix(target_board,"-reports.xml")
            target_board = remove_suffix(target_board,"-HWTestResults.xml")
            artifact_info_type=file_info
            return (file_name, file_info, target_board, artifact_info_type)

        raise Exception("Does not support non multilevel yet!")
        
    def get_payload_raw(self):
        payload = []
        file_path = os.path.join(FILE_DIR, self.file_name)
        try:
            if not os.path.exists(FILE_DIR):
                os.mkdir(FILE_DIR)
            grabber(self.url, file_path)
            # Parser
            xml = JUnitXml.fromfile(file_path)
            resultType = getattr(junitparser, self.file_info.split("_")[1].capitalize())
            for suite in xml:
                for case in suite:
                    if case.result and type(case.result[0]) is resultType:
                        payload.append(case.name)
        except Exception as ex:
            traceback.print_exc()
            print("Error Parsing File!")
        finally:
            os.remove(file_path)
        return payload
    
    def get_payload_parsed(self):
        num_payload = len(self.payload_raw)
        procedure = list(range(num_payload))
        param = list(range(num_payload))
        for k, payload_str in enumerate(self.payload_raw):
            # remove trailing adi.xxxx device name
            payload_str = re.sub("-adi\.\w*", "", payload_str)
            # remove multiple dashes
            payload_str = re.sub("-+", "-", payload_str)
            # replace () from MATLAB xml with []
            payload_str = payload_str.replace("(","[").replace(")","]")
            procedure_param = payload_str.split("[")
            procedure[k] = procedure_param[0]
            if len(procedure_param) == 2:
                # remove path from profile filename
                if any(x in procedure[k] for x in ["profile_write", "write_profile"]):
                    param[k] = re.findall("(\w*\..*)]",procedure_param[1])[0]
                else:
                    param[k] = procedure_param[1][:-1]
            else:
                param[k] = "NA"
        payload = procedure
        payload_param = param
        return (payload, payload_param)

class PytestFailure(xmlParser):
    def __init__(self, url):
        super(PytestFailure, self).__init__(url)
class PytestSkipped(xmlParser):
    def __init__(self, url):
        super(PytestSkipped, self).__init__(url)
class PytestError(xmlParser):
    def __init__(self, url):
        super(PytestError, self).__init__(url)

class MatlabFailure(xmlParser):
    def __init__(self, url):
        super(MatlabFailure, self).__init__(url)
class MatlabSkipped(xmlParser):
    def __init__(self, url):
        super(MatlabSkipped, self).__init__(url)
class MatlabError(xmlParser):
    def __init__(self, url):
        super(MatlabError, self).__init__(url)