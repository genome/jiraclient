#!/usr/bin/python
#
# jiraclient.py
#
# A Python Jira XML-RPC/SOAP Client
#
# (C) 2007,2008,2009,2010: Matthew Callaway
#
# jiraclient is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2, or (at your option) any later version.
#
# jiraclient.py is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You may have received a copy of the GNU General Public License along with
# jiraclient.py.  If not, write to the Free Software Foundation, Inc., 59
# Temple Place - Suite 330, Boston, MA 02111-1307, USA.

import getpass
import os
import pprint
import re
import sys
import time
import logging, logging.handlers
from stat import *
from optparse import OptionParser,OptionValueError
import ConfigParser
import xmlrpclib
import SOAPpy
import types

pp = pprint.PrettyPrinter(indent=4)
time_rx = re.compile('^\d+[mhdw]$')
session_rx = re.compile("session timed out")

def time_is_valid(value):
  def repl(m): return ''
  time = None
  m = time_rx.search(value)
  if not m:
    return False
  return True

def inspect(obj,padding=None):
  # Traverse an object printing non-private attributes
  if padding is None:
    padding = 5
  if type(obj) is types.StringType:
    print "%s%s" % (' ' * padding,obj)
  elif type(obj) is types.NoneType:
    print "%s%s" % (' ' * padding,'None')
  else:
    for attr in dir(obj):
      if attr.startswith('_'): continue
      a = getattr(obj,attr)
      if callable(a): continue
      if a.__class__ is SOAPpy.Types.structType:
        inspect(a,padding+5)
      if a.__class__ is SOAPpy.Types.typedArrayType:
        print "%s%s:" % (' ' * padding, attr)
        for x in a:
          inspect(x,padding+5)
      else:
        print "%s%s: %s" % (' ' * padding,attr,a)

class Issue(object):

  project = None
  type = None
  summary = None
  description = None
  assignee = None
  components = []
  fixVersions = []
  affectsVersions = []
  priority = None
  environment = None
  timetracking = None

  # This Issue class exists so that we can easily convert an
  # issue instance into a dictionary later.
  def __getitem__(self,key):
    item = getattr(self,key)
    if item is not None: return item

  def __repr__(self):
    text = "%s(" % (self.__class__.__name__)
    for attr in dir(self):
      if attr.startswith('_'): continue
      a = getattr(self,attr)
      if callable(a): continue
      text += "%s=%r," % (attr,a)
    text += ")"
    return text

  def update(self,key,value):
    if key == 'timetracking':
      if not time_is_valid(value):
        raise Exception("Illegal time value: %s" % value)

    if key == 'summary' and getattr(self,key) is not None:
      # Concatenate existing text, supports self.options.prefix
      newvalue = self.summary + value
      self.summary = newvalue
      return

    if type(getattr(self,key)) is list:
      if type(value) is list:
        setattr(self,key,value)
      else:
        # List types support split on comma
        value = "%s" % value
        setattr(self,key,[{'id':x} for x in value.split(',')])
    else:
      setattr(self,key,value)

class Jiraclient(object):

  version = "1.6.8"

  priorities = {}
  typemap = {}
  versionmap = {}
  componentmap = {}

  def fatal(self,msg=None):
    self.logger.fatal(msg)
    sys.exit(1)

  def print_version(self):
    print "jiraclient version %s" % self.version

  def parse_args(self):
    usage = """%prog [options]

 Sample Usage:
  - Standard issue creation in project named INFOSYS:
    jiraclient.py -u 'username' -p 'jirapassword' -A 'auser' -P INFOSYS -T task -S 'Do some task'

 - Get numerical Version IDs for Project named INFOSYS:
   jiraclient.py -u 'username' -p 'jirapassword' -a getVersions INFOSYS

 - Get numerical Component IDs for Project named INFOSYS:
   jiraclient.py -u 'username' -p 'jirapassword' -a getComponents INFOSYS

 - Create an issue with a specified Component and Fix Version 
   and assign it to myself:
   jiraclient.py -u 'username' -p 'jirapassword' -A 'username' -P INFOSYS -Q major -F 10000  -C 10003 -T epic -S 'Investigate Platform IFS'
"""
    optParser = OptionParser(usage)
    optParser.add_option(
      "--config",
      action="store",
      dest="config",
      help="Read configuration from this file",
      default=os.path.join(os.environ["HOME"],'.jiraclientrc'),
    )
    optParser.add_option(
      "-a","--api",
      action="store",
      dest="api",
      help="Call this API method",
      default=None,
    )
    optParser.add_option(
      "-c","--comment",
      action="store",
      dest="comment",
      help="Comment text",
      default=None,
    )
    optParser.add_option(
      "-l","--loglevel",
      type="choice",
      choices=["CRITICAL","ERROR","WARNING","INFO","DEBUG"],
      dest="loglevel",
      help="Set the log level",
      default="INFO",
    )
    optParser.add_option(
      "--link",
      action="store",
      dest="link",
      help="Given link=A,B link issues A and B",
      default=None,
    )
    optParser.add_option(
      "--unlink",
      action="store",
      dest="unlink",
      help="Given unlink=A,B unlink issues A and B",
      default=None,
    )
    optParser.add_option(
      "--subtask",
      action="store",
      dest="subtask",
      help="Given subtask=A,B Make issue B into a sub-task of A",
      default=None,
    )
    optParser.add_option(
      "--template",
      action="store",
      dest="template",
      help="Make a set of Issues based on a YAML template file",
      default=None,
    )
    optParser.add_option(
      "-n","--noop",
      action="store_true",
      dest="noop",
      help="Parse bug file but don't connect to Jira",
      default=False,
    )
    optParser.add_option(
      "-u","--user",
      action="store",
      dest="user",
      help="Jira user",
      default=None,
    )
    optParser.add_option(
      "-p","--password",
      action="store",
      dest="password",
      help="Jira password",
      default=None,
    )
    optParser.add_option(
      "-d","--display",
      action="store_true",
      dest="display",
      help="Display an existing given Jira issue ID",
      default=False,
    )
    optParser.add_option(
      "-i","--issue",
      action="store",
      dest="issueID",
      help="Jira issue ID (to modify)",
      default=None,
    )
    optParser.add_option(
      "-r","--remaining",
      action="store",
      dest="remaining",
      help="Jira issue time 'remaining estimate'",
      default=None,
    )
    optParser.add_option(
      "-t","--time",
      action="store",
      dest="timetracking",
      help="Jira issue time 'original estimate'",
      default=None,
    )
    optParser.add_option(
      "-A","--assignee",
      action="store",
      dest="assignee",
      help="Jira assignee",
      default=None,
    )
    optParser.add_option(
      "-C","--components",
      action="store",
      dest="components",
      help="Jira project components, comma separated list",
      default=None,
    )
    optParser.add_option(
      "-D","--description",
      action="store",
      dest="description",
      help="Jira issue description text",
      default=None,
    )
    optParser.add_option(
      "-E","--environment",
      action="store",
      dest="environment",
      help="Jira environment",
      default=None,
    )
    optParser.add_option(
      "-F","--fixVersions",
      action="store",
      dest="fixVersions",
      help="Jira project 'fix versions', comma separated list",
      default=None,
    )
    optParser.add_option(
      "-H","--epic",
      action="store",
      dest="issue_epic_theme",
      help="Set the epic/theme for the issue",
      default=None,
    )
    optParser.add_option(
      "-P","--project",
      action="store",
      dest="project",
      help="Jira project",
      default=None,
    )
    optParser.add_option(
      "-Q","--priority",
      action="store",
      dest="priority",
      help="Issue priority name",
      default=None,
    )
    optParser.add_option(
      "-S","--summary",
      action="store",
      dest="summary",
      help="Issue summary",
      default=None,
    )
    optParser.add_option(
      "-T","--type",
      action="store",
      dest="type",
      help="Issue type",
      default=None,
    )
    optParser.add_option(
      "-U","--jiraurl",
      action="store",
      dest="jiraurl",
      help="The Jira URL",
      default=None,
    )
    optParser.add_option(
      "-V","--affecstVersions",
      action="store",
      dest="affectsVersions",
      help="Jira project 'affects versions', comma separated list",
      default=None,
    )
    optParser.add_option(
      "--subtask-of",
      action="store",
      dest="subtask_of",
      help="Make the new issue a subtask of this issue key",
      default=None,
    )
    optParser.add_option(
      "--epic_theme",
      action="store",
      dest="epic_theme",
      help="Jira project 'Epic/Theme', custom field ID for the project (eg. customfield_10010)",
      default=None,
    )
    optParser.add_option(
      "--prefix",
      action="store",
      dest="prefix",
      help="Specify prefix text to prepend to all Issue summaries",
      default=None,
    )
    optParser.add_option(
      "--syslog",
      action="store_true",
      dest="use_syslog",
      help="Use syslog",
      default=False,
    )
    optParser.add_option(
      "-v","--version",
      action="store_true",
      dest="version",
      help="Version information",
      default=False,
    )
    (self.options, self.args) = optParser.parse_args()

  def prepare_logger(self):
    """prepares a logger optionally to use syslog and with a log level"""
    (use_syslog,loglevel) = (self.options.use_syslog,self.options.loglevel)

    logger = logging.getLogger("jiraclient")
    if use_syslog:
      handler = logging.handlers.SysLogHandler(address="/dev/log")
    else:
      handler = logging.StreamHandler()

    datefmt = "%b %d %H:%M:%S"
    fmt = "%(asctime)s %(name)s[%(process)d]: %(levelname)s: %(message)s"
    fmtr = logging.Formatter(fmt,datefmt)
    handler.setFormatter(fmtr)
    logger.handlers = []
    logger.addHandler(handler)
    logger.setLevel(logging._levelNames[loglevel])
    self.logger = logger

  def read_config(self):

    parser = ConfigParser.ConfigParser()
    parser.optionxform = str

    if self.options.config is not None:

      if not os.path.exists(self.options.config):
        # Write a basic rc file
        fd = open(self.options.config,'w')
        fd.write('# .jiraclientrc\n')
        fd.write('[jiraclient]\n')
        fd.write('jiraurl = \n')
        fd.write('user = %s\n' % os.environ["USER"])
        fd.write('[issues]\n')
        fd.write('#project = INFOSYS\n')
        fd.write('#type = story\n')
        fd.write('#priority = Normal\n')
        fd.write('#epic_theme = \n')
        fd.write('#assignee = \n')
        fd.write('#components = \n')
        fd.write('#fixVersions = \n')
        fd.close()
        os.chmod(self.options.config,int("600",8))

      stat = os.stat(self.options.config)
      if S_IMODE(os.stat(self.options.config).st_mode) != int("600",8):
        self.logger.warning("Config file %s is not mode 600" % (self.options.config))
      try:
        parser.readfp(file(self.options.config,'r'))
      except ConfigParser.ParsingError:
        self.logger.warning("Body has multiple lines, truncating...")
      except Exception, details:
        self.fatal("Unable to parse file at %r: %s" % (self.options.config,details))

    for (k,v) in (parser.items('jiraclient')):
      if not hasattr(self.options,k) or getattr(self.options,k) is None:
        setattr(self.options,k,v)

    # Map the items in the rc file to options, but only for issue *creation*
    if self.options.issueID is None:
      for (k,v) in (parser.items('issues')):
        if not hasattr(self.options,k):
          # You can't set in rcfile something that isn't also an option.
          self.fatal("Unknown issue attribute: %s" % k)
        if getattr(self.options,k) is None:
          # Take the rc file value if not given on CLI
          setattr(self.options,k,v)

  def get_project_id(self,project):
    result = self.proxy.getProjectsNoSchemes(self.auth)
    if result.__class__ is SOAPpy.Types.typedArrayType:
      for item in result:
        if hasattr(item,'key') and getattr(item,'key') == project:
          return item['id']
    else:
      for hash in result:
        if hash.has_key('key') and hash['key'] == project:
          return hash['id']

  def set_issue_types(self,projectID):
    result = self.proxy.getIssueTypesForProject(self.auth,projectID)
    for item in result:
      self.typemap[item['name'].lower()] = item['id']
    result = self.proxy.getSubTaskIssueTypesForProject(self.auth,projectID)
    for item in result:
      self.typemap[item['name'].lower()] = item['id']

  def set_project_versions(self,projectName):
    result = self.proxy.getVersions(self.auth,projectName)
    for item in result:
      self.versionmap[item['name'].lower()] = item['id']

  def set_project_components(self,projectName):
    result = self.proxy.getComponents(self.auth,projectName)
    for item in result:
      self.componentmap[item['name'].lower()] = item['id']

  def read_password(self):
    if not self.options.password:
      print "Please authenticate."
      pw = getpass.getpass("Jira password: ")
      self.options.password = pw

  def check_auth(self):
    session = os.path.join(os.environ["HOME"],'.jira-session')

    if os.path.exists(session):
      if S_IMODE(os.stat(session).st_mode) != int("600",8):
        self.logger.error("session file %s is not mode 600, forcing new session" % (session))
        os.unlink(session)

    if not os.path.exists(session):
      # New session
      self.read_password()
      try:
        auth = self.proxy.login(self.options.user,self.options.password)
      except Exception, details:
        self.fatal("Login failed")
      fd = open(session,'w')
      fd.write(auth)
      fd.close()
      os.chmod(session,int("600",8))
    else:
      # Existing auth session
      fd = open(session,'r')
      auth = fd.read()
      fd.close()

    self.auth = auth

    # We now have enough to connect,
    # so get priorities from Jira to validate auth.
    try:
      self.get_priorities()
    except Exception,details:
      m = session_rx.search(repr(details))
      if m:
        # Session expired, re-auth
        os.unlink(session)
        self.check_auth()
      else:
        self.fatal("Failed to get project priorities from Jira: %r" % (details))

  def get_priorities(self):
    result = self.proxy.getPriorities(self.auth)
    for item in result:
      self.priorities[item['name'].lower()] = item['id']

  def create_issue_obj(self,permissive=False):
    # Creates an Issue object based on CLI args and config file.
    # We do this for create and modify operations.

    issue = Issue()

    # Supported issue attributes, and whether or not they are required.
    # Requiredness might be better as some part of an Issue attribute.
    attrs = {
      'project'         : True,
      'type'            : True,
      'summary'         : True,
      'assignee'        : False,
      'components'      : False,
      'description'     : False,
      'fixVersions'     : False,
      'affectsVersions' : False,
      'priority'        : False,
      'environment'     : False,
      'timetracking'    : False,
    }

    # Allow 'None' on command line to unset something specified in .jiraclientrc
    for (key,value) in attrs.items():
      if hasattr(self.options,key) and getattr(self.options,key) is not None:
        if getattr(self.options,key).lower() == "none":
          setattr(self.options,key,None)

    # Validate that required options are present.
    for (key,required) in attrs.items():
      if hasattr(self.options,key) and getattr(self.options,key):
        # Timetracking must be a "modify" action, not create
        if key == 'timetracking' and not self.options.issueID: continue
        issue.update(key,getattr(self.options,key))
      else:
        if self.options.issueID is None:
          # This is a create, which requires some attrs
          if required is True and not permissive:
            self.fatal("You must specify: %s" % key)

    # Now that we have the project, get its ID and then project types.
    # Project ID is installation specific.
    if hasattr(issue,'project'):
      projectID = self.get_project_id(issue.project)
      if not projectID and self.options.issueID is None:
        self.fatal("Project %s is unknown" % issue.project)

      self.set_issue_types(projectID)
      self.set_project_versions(issue.project)
      self.set_project_components(issue.project)

    # A given type must be known to Jira, convert to numerical form
    if hasattr(issue,'type') and issue.type is not None:
      if issue.type not in self.typemap:
        print "Known issue types:\n%r\n" % self.typemap
        self.fatal("Unknown issue type: '%s' for Project: '%s'" % (issue.type,issue.project))
      issue.type = self.typemap[issue.type]

    # A given fixVersion must be known to Jira, convert to numerical form
    if hasattr(issue,'fixVersions'):
      for version in issue.fixVersions:
        versionid = version['id'].lower()
        if versionid.isdigit(): continue
        if versionid not in self.versionmap:
          print "Known versions :\n%r\n" % self.versionmap
          self.fatal("Unknown fixVersion: '%s' for Project: '%s'" % (versionid,issue.project))

        idx = issue.fixVersions.index(version)
        versionname = versionid
        versionid = self.versionmap[versionid]
        print "Version named '%s' is id %s" % (versionname,versionid)
        issue.fixVersions[idx]['id'] = versionid

    # A given Component must be known to Jira, convert to numerical form
    if hasattr(issue,'components'):
      for component in issue.components:
        componentid = component['id'].lower()
        if componentid.isdigit(): continue
        if componentid not in self.componentmap:
          print "Known components :\n%r\n" % self.componentmap
          self.fatal("Unknown component: '%s' for Project: '%s'" % (componentid,issue.project))

        idx = issue.components.index(component)
        componentname = componentid
        componentid = self.componentmap[componentid]
        print "Component named '%s' is id %s" % (componentname,componentid)
        issue.components[idx]['id'] = componentid

    # Epic/Theme is a custom field that may or may not be enabled
    # for a given project.  We have the issue_epic_theme which is the desired
    # existing Epic issue we want to include in the issue we're creating,
    # and we have the epic_theme, which is the custom field ID for this 
    # installation's current Project.
    if self.options.issue_epic_theme:
      issue.customFieldValues = [{'values':self.options.issue_epic_theme,'customfieldId':self.options.epic_theme}]

    # Priorities are also installation specific
    if hasattr(issue,'priority') and issue.priority is not None:
      issue.priority = self.priorities[issue.priority.lower()]

    # Set prefix on summary
    if self.options.prefix:
      issue.summary = self.options.prefix

    return issue

  def get_issue(self,issueID):
    result = self.proxy.getIssue(self.auth,issueID)
    return result

  def get_issue_links(self,issueID,type=None):
    if self.proxy.__class__ is not SOAPpy.WSDL.Proxy:
      self.fatal("Only the SOAP client can link issues")
    result = self.proxy.getIssue(self.auth,issueID)
    result = self.proxy.getLinkedIssues(self.auth,SOAPpy.Types.longType(long(result['id'])),'')
    return result

  def add_comment(self,issueID,comment):
    result = self.proxy.addComment(self.auth,issueID,comment)
    print "Modified %s/browse/%s" % \
     (self.proxy.getServerInfo(self.auth)['baseUrl'], issueID)

  def update_estimate(self,estimate,issueID):
    if self.proxy.__class__ is not SOAPpy.WSDL.Proxy:
      self.logger.error("Only the SOAP interface supports this operation")
      return

    m = time_rx.match(estimate)
    if not m:
      self.logger.warning("Time estimate has dubious format: %s: no action taken" % (estimate))
      return

    # Note timeSpent must be set, and cannot be less than 1m
    dt_today = SOAPpy.dateTimeType(time.gmtime(time.time())[:6])
    worklog = {'startDate':dt_today,'timeSpent':'1m','comment':'jiraclient updates remaining estimate to %s' % estimate}
    if self.options.noop:
      self.logger.info("Would update time remaining: %s: %s" % (issueID,estimate))
      return
    self.logger.info("Update time remaining: %s: %s" % (issueID,estimate))
    self.proxy.addWorklogWithNewRemainingEstimate(self.auth, issueID, worklog, estimate)

  def modify_issue(self,issueID,issue):

    issue = issue.__dict__

    if self.proxy.__class__ is SOAPpy.WSDL.Proxy:
      # SOAP takes a list of dictionaries of parameters, we need to convert to
      # the right format
      paramlist = []
      for item in issue:
        params = {'id':None,'values':None}
        if item == 'type':
          params['id'] = 'issuetype' # type becomes issuetype? WTF?
        else:
          params['id'] = item
        if type(issue[item]) is list:
          params['values'] = [x.values()[0] for x in issue[item]]
        else:
          params['values'] = issue[item]
        if params not in paramlist:
          paramlist.append(params)
      issue = paramlist

    print "Modify %s:" % issueID
    pp.pprint(issue)
    if self.options.noop: return
    result = self.proxy.updateIssue(self.auth,issueID,issue)

    print "Modified %s/browse/%s" % \
     (self.proxy.getServerInfo(self.auth)['baseUrl'], issueID)

  def link_issues(self,issueFrom,linkType,issueTo):
    if self.options.noop:
      print "Would link %s -> %s" % (parent,child)
      return
    if self.proxy.__class__ is not SOAPpy.WSDL.Proxy:
      self.fatal("Only the SOAP client can link issues")
    fromIssue = self.proxy.getIssue(self.auth,issueFrom)
    fromId = SOAPpy.Types.longType(long(fromIssue['id']))
    toIssue = self.proxy.getIssue(self.auth,issueTo)
    toId = SOAPpy.Types.longType(long(toIssue['id']))
    result = self.proxy.linkIssue(self.auth,fromId,toId,linkType,True,False)
    return result

  def unlink_issues(self,issueFrom,linkType,issueTo):
    if self.options.noop:
      print "Would unlink %s -> %s" % (parent,child)
      return
    if self.proxy.__class__ is not SOAPpy.WSDL.Proxy:
      self.fatal("Only the SOAP client can link issues")
    fromIssue = self.proxy.getIssue(self.auth,issueFrom)
    fromId = SOAPpy.Types.longType(long(fromIssue['id']))
    toIssue = self.proxy.getIssue(self.auth,issueTo)
    toId = SOAPpy.Types.longType(long(toIssue['id']))
    result = self.proxy.unlinkIssue(self.auth,fromId,toId,linkType)
    return result

  def subtask_link(self,parent,child):
    if self.options.noop:
      print "Would apply subtask link"
      return
    if self.proxy.__class__ is not SOAPpy.WSDL.Proxy:
      self.fatal("Only the SOAP client can link issues")
    parent = self.proxy.getIssue(self.auth,parent)
    parentId = SOAPpy.Types.longType(long(parent['id']))
    child = self.proxy.getIssue(self.auth,child)
    childId = SOAPpy.Types.longType(long(child['id']))

    # issueType is always jira_subtask_link, which we get from the Jira DB:
    # mysql> select * from issuelinktype;
    # +-------+-------------------+---------------------+----------------------+--------------+
    # | ID    | LINKNAME          | INWARD              | OUTWARD              | pstyle       |
    # +-------+-------------------+---------------------+----------------------+--------------+
    # | 10010 | Duplicate         | is duplicated by    | duplicates           | NULL         | 
    # | 10000 | jira_subtask_link | jira_subtask_inward | jira_subtask_outward | jira_subtask | 
    # | 10011 | Depends           | is depended on by   | depends on           | NULL         | 
    # | 10012 | Blocks            | is blocked by       | blocks               | NULL         | 
    # +-------+-------------------+---------------------+----------------------+--------------+
    # 4 rows in set (0.00 sec)
    linkType = SOAPpy.Types.longType(long(10000))

    # If we don't already know issue types, figure them out...
    projectID = self.get_project_id(child.project)
    if not projectID:
      self.fatal("Project %s is unknown" % child.project)

    # Set sub-task link
    result = self.proxy.createSubtaskLink(self.auth,parentId,childId,linkType)

    # Set issue type to sub-task after linking.
    issue = Issue()
    issue.type = self.typemap['sub-task']
    issue.project = projectID
    self.modify_issue(child.key,issue)

    return result

  def create_issue(self,issue):

    issue = issue.__dict__
    print "Create issue:"
    pp.pprint(issue)
    if self.options.noop:
      # return a fake Issue Key
      return 'issue'
    newissue = self.proxy.createIssue(self.auth,issue)
    issueID = newissue["key"]
    print "Created %s/browse/%s" % \
     (self.proxy.getServerInfo(self.auth)['baseUrl'], issueID)

    return issueID

  def create_issues_from_template(self):
    # FIXME: This method is too long and has opportunities to reduce duplicate code.

    if self.proxy.__class__ is not SOAPpy.WSDL.Proxy:
      self.fatal("Only the SOAP extended webservice supports issue templates")

    # Epic is required on templates to satisfy a GC convention.
    if not hasattr(self.options,'epic_theme'):
      self.fatal("Configuration lacking epic_theme parameter")

    import yaml

    if not self.options.template == "-" and not os.path.exists(self.options.template):
      self.fatal("No such file: %s" % self.options.template)

    # This isn't "real" recursion because as we get deeper the thing we represent
    # goes from Epic to Story to Subtask, which are different datatypes in Jira.
    try:
      if self.options.template == "-":
        template = yaml.load(sys.stdin)
      else:
        template = yaml.load(file(self.options.template,"r"))
    except Exception,details:
      self.fatal("Failed to parse YAML template: %s" % details)

    e = self.create_issue_obj(permissive=True)
    e.type = self.typemap['epic']
    # FIXME: Assumes we have a single epic in this template
    for (key,value) in template.items():
      if type(value) is list: continue
      if type(value) is types.StringType or type(value) is int:
        if hasattr(e,key):
          e.update(key,value)
        else:
          self.fatal("Unknown issue attribute in template: %s" % (key))

    # Create an epic Issue here
    eid = self.create_issue(e)
    # set epic/theme with Issue ID we just received
    e = Issue()
    e.customFieldValues = [{'values':eid,'customfieldId':self.options.epic_theme}]
    self.modify_issue(eid,e)

    time = None
    if 'subtasks' in template.keys():
      for subtask in template['subtasks']:
        st = self.create_issue_obj(permissive=True)
        # You cannot directly create a sub-task, you have to
        # create an issue, set sub-task link, then set type = sub-task
        st.type = self.typemap['story']
        for (key,value) in subtask.items():
          if type(value) is list:
            self.fatal("Unsupported nesting depth in subtask of story: %s" % subtask['summary'])
          if type(value) is types.StringType or type(value) is int:
            if hasattr(st,key):
              st.update(key,value)
            else:
              self.fatal("Unknown issue attribute in template: %s" % (key))

        st.customFieldValues = [{'values':eid,'customfieldId':self.options.epic_theme}]
        # We specify timetracking on issues, but we can only set
        # that attribute on a Modify action, not a Create action.
        if hasattr(st,'timetracking') and st.timetracking is not None:
          time = st.timetracking
          del st.timetracking
        stid = self.create_issue(st)
        # This converts to a sub-task
        self.subtask_link(eid,stid)
        # Now set timetracking
        if time is not None:
          st = Issue()
          st.update('timetracking',time)
          self.modify_issue(stid,st)
          time = None

    # Now create the stories
    if 'stories' in template.keys():
      for story in template['stories']:
        s = self.create_issue_obj(permissive=True)
        s.type = self.typemap['story']
        for (key,value) in story.items():
          if type(value) is list: continue
          if type(value) is types.StringType or type(value) is int:
            if hasattr(s,key):
              s.update(key,value)
            else:
              self.fatal("Unknown issue attribute in template: %s" % (key))

        time = None
        # We specify timetracking on issues, but we can only set
        # that attribute on a Modify action, not a Create action.
        if hasattr(s,'timetracking') and s.timetracking is not None:
          time = s.timetracking
          del s.timetracking

        if eid:
          s.customFieldValues = [{'values':eid,'customfieldId':self.options.epic_theme}]
        sid = self.create_issue(s)
        if time is not None:
          # Now set the timetracking
          s = Issue()
          s.update('timetracking',time)
          self.modify_issue(sid,s)

        # Now create all subtasks of this story
        time = None
        if 'subtasks' in story:
          for subtask in story['subtasks']:
            st = self.create_issue_obj(permissive=True)
            # You cannot directly create a sub-task, you have to
            # create an issue, set sub-task link, then set type = sub-task
            st.type = self.typemap['story']
            for (key,value) in subtask.items():
              if type(value) is list:
                self.fatal("Unsupported nesting depth in subtask of story: %s" % subtask['summary'])
              if type(value) is types.StringType or type(value) is int:
                if hasattr(st,key):
                  st.update(key,value)
                else:
                  self.fatal("Unknown issue attribute in template: %s" % (key))

            st.customFieldValues = [{'values':eid,'customfieldId':self.options.epic_theme}]
            # We specify timetracking on issues, but we can only set
            # that attribute on a Modify action, not a Create action.
            if hasattr(st,'timetracking') and st.timetracking is not None:
              time = st.timetracking
              del st.timetracking
            stid = self.create_issue(st)
            # This converts to a sub-task
            self.subtask_link(sid,stid)
            # Now set timetracking
            if time is not None:
              st = Issue()
              st.update('timetracking',time)
              self.modify_issue(stid,st)
              time = None

    if not self.options.noop:
      print "Issue Filter: %s/secure/IssueNavigator.jspa?reset=true&jqlQuery=cf[%s]+%%3D+%s+ORDER+BY+key+ASC,issuetype+ASC" % (self.proxy.getServerInfo(self.auth)['baseUrl'],self.options.epic_theme.replace('customfield_',''),eid)

  def call_API(self,api,args):
    func = getattr(self.proxy,api)
    args = ' '.join(args)
    if args:
      result = func(self.auth,args)
    else:
      result = func(self.auth)

    if self.proxy.__class__ is SOAPpy.WSDL.Proxy:
      for item in result:
        inspect(item)

    else:
      print result

      for item in result:
        if type(item) is dict:
          for (k,v) in item.items():
            print "%s = %s" % (k,v)
          print
        else:
          print result

  def run(self):

    self.parse_args()

    if self.options.version:
      self.print_version()
      return

    self.prepare_logger()
    self.read_config()

    if not self.options.user:
      self.fatal("Please specify Jira user")

    if not self.options.jiraurl:
      self.fatal("Please specify the Jira URL")

    if self.options.jiraurl.lower().find('soap') != -1:
      self.proxy = SOAPpy.WSDL.Proxy(self.options.jiraurl)
    else:
      self.proxy = xmlrpclib.ServerProxy(self.options.jiraurl).jira1

    # Check or get auth token
    self.check_auth()

    # Run a named Jira API call and return
    if self.options.api is not None:
      try:
        self.call_API(self.options.api,self.args)
      except Exception, details:
        self.fatal("API error: bad method or args: %s" % details)
      return

    # Create a set of Issues based on a YAML project file
    if self.options.template is not None:
      self.create_issues_from_template()
      return

    # Link two existing IDs
    if self.options.link is not None:
      (fromId,type,toId) = self.options.link.split(',')
      print "Create '%s' link from '%s' to '%s'" % (type,fromId,toId)
      result = self.link_issues(fromId,type,toId)
      return

    # UnLink two existing IDs
    if self.options.unlink is not None:
      (fromId,type,toId) = self.options.unlink.split(',')
      print "Remove '%s' link from '%s' to '%s'" % (type,fromId,toId)
      result = self.unlink_issues(fromId,type,toId)
      return

    # Make one issue a sub-task of another
    if self.options.subtask is not None:
      (parent,child) = self.options.subtask .split(',')
      print "Make '%s' a subtask of '%s'" % (child,parent)
      result = self.subtask_link(parent,child)
      return

    # Comment on an existing issue ID
    if self.options.comment is not None:
      if self.options.issueID is None:
        self.fatal("Specify an issue ID to comment on")
      else:
        if self.options.noop:
          print "Add comment to %s: %s" % (self.options.issueID,self.options.comment)
          return
        self.add_comment(self.options.issueID,self.options.comment)
        return

    # Display a specified issue ID
    if self.options.display:

      if self.options.issueID is None:
        self.logger.error("Please specify an issue ID")
        return

      # Get the issue to display
      try:
        issue = self.get_issue(self.options.issueID)
        print "Issue: %s" % self.options.issueID
      except Exception, details:
        print "There was an error fetching %s. Reason: %r" % (self.options.issueID,details)
        return

      if issue.__class__ is SOAPpy.Types.structType:
        inspect(issue)

      else:
        for (k,v) in issue.items():
          print "%15s: %s" % (k,v)

      # Display linked issues... only with SOAP
      if issue.__class__ is SOAPpy.Types.structType:
        links = self.get_issue_links(self.options.issueID)
        ids = []
        for link in links:
          ids.append(getattr(link,'key'))
        print "Linked to this issue:"
        print "%r" % ids

      return

    # Update time remaining of given issue
    if self.options.remaining is not None:
      self.update_estimate(self.options.remaining,self.options.issueID)
      return

    # Create an issue object
    issue = self.create_issue_obj()

    # Modify existing issue
    if self.options.issueID is not None:
      # Modify existing issue
      self.modify_issue(self.options.issueID,issue)
      return

    # Create a new issue
    try:
      issueID = self.create_issue(issue)
    except Exception, details:
      self.fatal("Failed to create issue.  Reason: %r" % details)

    # Make the issue a subtask, if a parent is given
    if self.options.subtask_of:
      self.subtask_link(self.options.subtask_of,issueID)

    # Set timetracking if present
    if self.options.timetracking is not None:
      tt = Issue()
      tt.timetracking = self.options.timetracking
      self.modify_issue(issueID,tt)

def main():
  A = Jiraclient()
  try:
    A.run()
  except KeyboardInterrupt:
    print "Exiting..."
    return

if __name__ == "__main__":
  main()

