"""Mypy static type checker plugin for Pytest"""

import os
import warnings

import pytest
import mypy.api


mypy_argv = []


def pytest_addoption(parser):
    """Add options for enabling and running mypy."""
    group = parser.getgroup('mypy')
    group.addoption(
        '--mypy', action='store_true',
        help='run mypy on .py files')
    group.addoption(
        '--mypy-files', action='store_true',
        help='Do not invoke mypy on files collected by pytest.'
             ' Use this to enable specifying files in mypy.ini.')
    group.addoption(
        '--mypy-ignore-missing-imports', action='store_true',
        help="suppresses error messages about imports that cannot be resolved")


def pytest_configure(config):
    """
    Register a custom marker for MypyItems,
    and configure the plugin based on the CLI.
    """
    config.addinivalue_line(
        'markers',
        '{marker}: mark tests to be checked by mypy.'.format(
            marker=MypyItem.MARKER,
        ),
    )
    if config.getoption('--mypy-ignore-missing-imports'):
        mypy_argv.append('--ignore-missing-imports')


def pytest_collect_file(path, parent):
    """Create a MypyFileItem for every file mypy should run on."""
    if path.ext == '.py' and any([
            parent.config.option.mypy,
            parent.config.option.mypy_ignore_missing_imports,
            parent.config.option.mypy_files,
    ]):
        return MypyFileItem(path, parent)
    return None


def _mypy_file_items(items):
    return {
        os.path.abspath(str(item.fspath)): item
        for item in items
        if isinstance(item, MypyFileItem)
    }


def pytest_collection_modifyitems(session, config, items):
    """
    Add a MypyStatusItem if --mypy-files is used.

    With --mypy-files, mypy might check files that were not collected.
    In that case, pytest could pass even though mypy failed.
    To prevent that, add an explicit check for the mypy exit status.
    """
    if config.option.mypy_files and _mypy_file_items(items):
        items.append(MypyStatusItem('mypy', session, config, session))


def pytest_runtestloop(session):
    """Run mypy on collected MypyFileItems, then sort the output."""
    mypy_file_items = _mypy_file_items(session.items)
    if mypy_file_items:

        terminal = session.config.pluginmanager.getplugin('terminalreporter')
        files = [] if session.config.option.mypy_files else [
            str(item.fspath) for item in mypy_file_items.values()
        ]
        terminal.write(
            '\nRunning {command}{on_files}... '.format(
                command=' '.join(['mypy'] + mypy_argv),
                on_files='' if not files else ' on {file_count} files'.format(
                    file_count=len(files),
                ),
            ),
        )
        stdout, stderr, status = mypy.api.run(mypy_argv + files)
        terminal.write('done with status {status}\n'.format(status=status))

        for item in session.items:
            if isinstance(item, MypyStatusItem):
                item.mypy_status = status

        unmatched_lines = []
        for line in stdout.split('\n'):
            if not line:
                continue
            mypy_path, _, error = line.partition(':')
            try:
                item = mypy_file_items[os.path.abspath(mypy_path)]
            except KeyError:
                unmatched_lines.append(line)
            else:
                item.mypy_errors.append(error)
        if any(unmatched_lines):
            color = {"red": True} if status != 0 else {"green": True}
            terminal.write_line('\n'.join(unmatched_lines), **color)

        if stderr:
            terminal.write_line(stderr, red=True)


class MypyItem(pytest.Item):

    """A Mypy-related test Item."""

    MARKER = 'mypy'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.add_marker(self.MARKER)

    def repr_failure(self, excinfo):
        """
        Unwrap mypy errors so we get a clean error message without the
        full exception repr.
        """
        if excinfo.errisinstance(MypyError):
            return excinfo.value.args[0]
        return super().repr_failure(excinfo)


class MypyFileItem(MypyItem, pytest.File):

    """A File that Mypy Runs On."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mypy_errors = []

    def reportinfo(self):
        """Produce a heading for the test report."""
        return (
            self.fspath,
            None,
            self.config.invocation_dir.bestrelpath(self.fspath),
        )

    def runtest(self):
        """Raise an exception if mypy found errors for this item."""
        if self.mypy_errors:
            raise MypyError('\n'.join(self.mypy_errors))
        elif self.config.option.mypy_files:
            warnings.warn(
                MypyWarning(
                    'No mypy errors were detected in this file,'
                    ' but since --mypy-files does not require'
                    ' mypy to check files collected by pytest,'
                    ' pytest-mypy cannot be sure that it was'
                    ' actually checked.',
                ),
            )


class MypyStatusItem(MypyItem):

    """A check for a non-zero mypy exit status."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mypy_status = None

    def runtest(self):
        """Raise a MypyError if mypy exited with a non-zero status."""
        assert self.mypy_status is not None
        if self.mypy_status:
            raise MypyError(
                'mypy exited with status {0}.'.format(self.mypy_status),
            )


class MypyError(Exception):
    """
    An error caught by mypy, e.g a type checker violation
    or a syntax error.
    """


class MypyWarning(getattr(pytest, 'PytestWarning', UserWarning)):
    """A warning regarding mypy."""
