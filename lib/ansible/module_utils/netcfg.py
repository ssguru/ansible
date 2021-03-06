#
# (c) 2015 Peter Sprygada, <psprygada@ansible.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.
#

import re
import time
import itertools
import shlex
import itertools

from ansible.module_utils.basic import BOOLEANS_TRUE, BOOLEANS_FALSE
from ansible.module_utils.network import to_list

DEFAULT_COMMENT_TOKENS = ['#', '!']


class ConfigLine(object):

    def __init__(self, text):
        self.text = text
        self.children = list()
        self.parents = list()
        self.raw = None

    @property
    def line(self):
        line = ['set']
        line.extend([p.text for p in self.parents])
        line.append(self.text)
        return ' '.join(line)

    def __str__(self):
        return self.raw

    def __eq__(self, other):
        if self.text == other.text:
            return self.parents == other.parents

    def __ne__(self, other):
        return not self.__eq__(other)

def ignore_line(text, tokens=None):
    for item in (tokens or DEFAULT_COMMENT_TOKENS):
        if text.startswith(item):
            return True

def get_next(iterable):
    item, next_item = itertools.tee(iterable, 2)
    next_item = itertools.islice(next_item, 1, None)
    return itertools.izip_longest(item, next_item)

def parse(lines, indent, comment_tokens=None):
    toplevel = re.compile(r'\S')
    childline = re.compile(r'^\s*(.+)$')

    ancestors = list()
    config = list()

    for line in str(lines).split('\n'):
        text = str(re.sub(r'([{};])', '', line)).strip()

        cfg = ConfigLine(text)
        cfg.raw = line

        if not text or ignore_line(text, comment_tokens):
            continue

        # handle top level commands
        if toplevel.match(line):
            ancestors = [cfg]

        # handle sub level commands
        else:
            match = childline.match(line)
            line_indent = match.start(1)
            level = int(line_indent / indent)
            parent_level = level - 1

            cfg.parents = ancestors[:level]

            if level > len(ancestors):
                config.append(cfg)
                continue

            for i in range(level, len(ancestors)):
                ancestors.pop()

            ancestors.append(cfg)
            ancestors[parent_level].children.append(cfg)

        config.append(cfg)

    return config

def dumps(objects, output='block'):
    if output == 'block':
        items = [c.raw for c in objects]
    elif output == 'commands':
        items = [c.text for c in objects]
    elif output == 'lines':
        items = list()
        for obj in objects:
            line = list()
            line.extend([p.text for p in obj.parents])
            line.append(obj.text)
            items.append(' '.join(line))
    return '\n'.join(items)

class NetworkConfig(object):

    def __init__(self, indent=None, contents=None, device_os=None):
        self.indent = indent or 1
        self._config = list()
        self._device_os = device_os
        self._syntax = 'block' # block, lines, junos

        if self._device_os == 'junos':
            self._syntax = 'junos'

        if contents:
            self.load(contents)

    @property
    def items(self):
        return self._config

    def __str__(self):
        if self._device_os == 'junos':
            return dumps(self.expand_line(self.items), 'lines')
        return dumps(self.expand_line(self.items))

    def load(self, contents):
        self._config = parse(contents, indent=self.indent)

    def load_from_file(self, filename):
        self.load(open(filename).read())

    def get(self, path):
        if isinstance(path, basestring):
            path = [path]
        for item in self._config:
            if item.text == path[-1]:
                parents = [p.text for p in item.parents]
                if parents == path[:-1]:
                    return item

    def get_object(self, path):
        for item in self.items:
            if item.text == path[-1]:
                parents = [p.text for p in item.parents]
                if parents == path[:-1]:
                    return item

    def get_section_objects(self, path):
        if not isinstance(path, list):
            path = [path]
        obj = self.get_object(path)
        if not obj:
            raise ValueError('path does not exist in config')
        return self.expand_section(obj)

    def search(self, regexp, path=None):
        regex = re.compile(r'^%s' % regexp, re.M)

        if path:
            parent = self.get(path)
            if not parent or not parent.children:
                return
            children = [c.text for c in parent.children]
            data = '\n'.join(children)
        else:
            data = str(self)

        match = regex.search(data)
        if match:
            if match.groups():
                values = match.groupdict().values()
                groups = list(set(match.groups()).difference(values))
                return (groups, match.groupdict())
            else:
                return match.group()

    def findall(self, regexp):
        regexp = r'%s' % regexp
        return re.findall(regexp, str(self))

    def expand_line(self, objs):
        visited = set()
        expanded = list()
        for o in objs:
            for p in o.parents:
                if p not in visited:
                    visited.add(p)
                    expanded.append(p)
            expanded.append(o)
            visited.add(o)
        return expanded

    def expand_section(self, configobj, S=None):
        if S is None:
            S = list()
        S.append(configobj)
        for child in configobj.children:
            if child in S:
                continue
            self.expand_section(child, S)
        return S

    def expand_block(self, objects, visited=None):
        items = list()

        if not visited:
            visited = set()

        for o in objects:
            items.append(o)
            visited.add(o)
            for child in o.children:
                items.extend(self.expand_block([child], visited))

        return items

    def diff_line(self, other):
        diff = list()
        for item in self.items:
            if item not in other.items:
                diff.append(item)
        return diff

    def diff_strict(self, other):
        diff = list()
        for index, item in enumerate(self.items):
            try:
                if item != other.items[index]:
                    diff.append(item)
            except IndexError:
                diff.append(item)
        return diff

    def diff_exact(self, other):
        diff = list()
        if len(other.items) != len(self.items):
            diff.extend(self.items)
        else:
            for ours, theirs in itertools.izip(self.items, other.items):
                if ours != theirs:
                    diff.extend(self.items)
                    break
        return diff


    def difference(self, other, match='line', replace='line'):
        try:
            func = getattr(self, 'diff_%s' % match)
            updates = func(other)
        except AttributeError:
            raise TypeError('invalid value for match keyword')

        if self._device_os == 'junos':
            return updates

        if replace == 'block':
            parents = list()
            for u in updates:
                if u.parents is None:
                    if u not in parents:
                        parents.append(u)
                else:
                    for p in u.parents:
                        if p not in parents:
                            parents.append(p)

            return self.expand_block(parents)

        return self.expand_line(updates)

    def replace(self, patterns, repl, parents=None, add_if_missing=False,
                ignore_whitespace=True):

        match = None

        parents = to_list(parents) or list()
        patterns = [re.compile(r, re.I) for r in to_list(patterns)]

        for item in self.items:
            for regexp in patterns:
                text = item.text
                if not ignore_whitespace:
                    text = item.raw
                if regexp.search(text):
                    if item.text != repl:
                        if parents == [p.text for p in item.parents]:
                            match = item
                            break

        if match:
            match.text = repl
            indent = len(match.raw) - len(match.raw.lstrip())
            match.raw = repl.rjust(len(repl) + indent)

        elif add_if_missing:
            self.add(repl, parents=parents)


    def add(self, lines, parents=None):
        """Adds one or lines of configuration
        """

        ancestors = list()
        offset = 0
        obj = None

        ## global config command
        if not parents:
            for line in to_list(lines):
                item = ConfigLine(line)
                item.raw = line
                if item not in self.items:
                    self.items.append(item)

        else:
            for index, p in enumerate(parents):
                try:
                    i = index + 1
                    obj = self.get_section_objects(parents[:i])[0]
                    ancestors.append(obj)

                except ValueError:
                    # add parent to config
                    offset = index * self.indent
                    obj = ConfigLine(p)
                    obj.raw = p.rjust(len(p) + offset)
                    if ancestors:
                        obj.parents = list(ancestors)
                        ancestors[-1].children.append(obj)
                    self.items.append(obj)
                    ancestors.append(obj)

            # add child objects
            for line in to_list(lines):
                # check if child already exists
                for child in ancestors[-1].children:
                    if child.text == line:
                        break
                else:
                    offset = len(parents) * self.indent
                    item = ConfigLine(line)
                    item.raw = line.rjust(len(line) + offset)
                    item.parents = ancestors
                    ancestors[-1].children.append(item)
                    self.items.append(item)


