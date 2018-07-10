"""Wrapper functions for git commands.

This module contains wrapper functions for git commands, such as push and clone.
"""
import os
import sys
import subprocess
import collections
import daiquiri
import asyncio
from typing import Sequence, Tuple, Iterable, List, Any, Callable

from gits_pet import util

CONCURRENT_TASKS = 20

LOGGER = daiquiri.getLogger(__file__)

Push = collections.namedtuple('Push', ('local_path', 'repo_url', 'branch'))

OAUTH_TOKEN = os.getenv('GITS_PET_OAUTH')
if not OAUTH_TOKEN:
    raise OSError('The oauth token is empty!')


class GitError(Exception):
    """A generic error to raise when a git command exits with a non-zero
    exit status.
    """

    def __init__(self, msg: str, returncode: int, stderr: bytes):
        msg_ = ("{}{}"
                "return code: {}{}"
                "stderr: {}").format(
                    msg,
                    os.linesep,
                    returncode,
                    os.linesep,
                    stderr.decode(encoding=sys.getdefaultencoding()))
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(msg_)


class CloneFailedError(GitError):
    """An error to raise when cloning a repository fails."""

    def __init__(self, msg: str, returncode: int, stderr: bytes, url: str):
        self.url = url
        super().__init__(msg, returncode, stderr)


class PushFailedError(GitError):
    """An error to raise when pushing to a remote fails."""

    def __init__(self, msg: str, returncode: int, stderr: bytes, url: str):
        self.url = url
        super().__init__(msg, returncode, stderr)


def _insert_token(https_url: str, token: str = OAUTH_TOKEN) -> str:
    """Insert a token into the https url as described here:
        https://blog.github.com/2012-09-21-easier-builds-and-deployments-using-git-over-https-and-oauth/

    Args:
        https_url: A url on the form `https://host.topdomain`
        token: A GitHub OAUTH token, with or without username (e.g. on the form
        `<token>` or `<username>:<token>`)

    Returns:
        The provided url with the token inserted
    """
    if not https_url.startswith('https://'):
        raise ValueError(
            'invalid url `{}`, does not start with `https://`'.format(
                https_url))
    if not token:
        raise ValueError('invalid token, empty token not allowed')
    return https_url.replace('https://', 'https://{}@'.format(token))


def _insert_user_and_token(https_url: str, user: str,
                           token: str = OAUTH_TOKEN) -> str:
    """Insert a username and an oauth token into the https url as described here:
        https://blog.github.com/2012-09-21-easier-builds-and-deployments-using-git-over-https-and-oauth/

    Args:
        https_url: A url on the form `https://host.topdomain`
        user: A GitHub username.
        token: A GitHub OAUTH token.

    Returns:
        The provided url with the username and token inserted
    """
    return _insert_token(https_url, "{}:{}".format(user, token))


def captured_run(*args, **kwargs):
    """Run a subprocess and capture the output."""
    proc = subprocess.run(
        *args, **kwargs, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return proc.returncode, proc.stdout, proc.stderr


def clone_single(repo_url: str, single_branch: bool = True,
                 branch: str = None):
    """Clone a git repository.

    Args:
        repo_url: HTTPS url to repository on the form https://<host>/<owner>/<repo>.
        single_branch: Whether or not to clone a single branch.
        branch: The branch to clone.
    """
    util.validate_types(
        repo_url=(repo_url, str),
        single_branch=(single_branch, bool),
        branch=(branch, (str, type(None))))

    if isinstance(branch, str) and not branch:
        raise ValueError("branch must not be empty")

    options = []
    if single_branch:
        options.append('--single-branch')
    if branch is not None:
        options += ['-b', branch]

    clone_command = [
        'git', 'clone',
        _insert_token(repo_url, OAUTH_TOKEN), *options
    ]
    rc, _, stderr = captured_run(clone_command)

    if rc != 0:
        raise CloneFailedError("Failed to clone", rc, stderr, repo_url)


async def _clone_async(repo_url: str,
                       single_branch: bool = True,
                       branch: str = None):
    """Clone git repositories asynchronously.

    Args:
        repo_url: A url to clone.
        single_branch: Whether to clone a single branch or not.
        branch: Which branch to clone.
    """
    command = ['git', 'clone', _insert_token(repo_url)]
    if single_branch:
        command.append('--single-branch')
    proc = await asyncio.create_subprocess_exec(
        *command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise CloneFailedError(
            "Failed to clone {}".format(repo_url),
            returncode=proc.returncode,
            stderr=stderr,
            url=repo_url)
    else:
        LOGGER.info("Cloned into {}".format(repo_url))


def clone(repo_urls: Iterable[str],
          single_branch: bool = True) -> List[Exception]:
    """Clone all repos asynchronously.

    Args:
        repo_urls: URLs to repos to clone.
        single_branch: Whether or not to clone only the default branch.

    Returns:
        URLs from which cloning failed.
    """
    # TODO valdate repo_urls
    util.validate_types(single_branch=(single_branch, bool))
    util.validate_non_empty(repo_urls=repo_urls, single_branch=single_branch)

    return [
        exc.url
        for exc in _batch_execution(_clone_async, repo_urls, single_branch)
        if isinstance(exc, CloneFailedError)
    ]


async def _push_async(pt: Push, user: str):
    """Asynchronous call to git push, pushing directly to the repo_url and branch.

    Args:
        pt: A Push namedtuple.
        user: The username to use in the push.
    """
    util.validate_types(push_tuple=(pt, Push), user=(user, str))

    util.validate_non_empty(user=user)

    command = [
        'git', 'push',
        _insert_user_and_token(pt.repo_url, user, OAUTH_TOKEN), pt.branch
    ]
    proc = await asyncio.create_subprocess_exec(
        *command,
        cwd=os.path.abspath(pt.local_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise PushFailedError("Failed to push to {}".format(pt.repo_url),
                              proc.returncode, stderr, pt.repo_url)
    elif b"Everything up-to-date" in stderr:
        LOGGER.info("{} is up-to-date".format(pt.repo_url))
    else:
        LOGGER.info("Pushed files to {} {}".format(pt.repo_url, pt.branch))


def push_single(local_repo: str,
                user: str,
                repo_url: str,
                branch: str = 'master'):
    """Push from a local repository to a remote repository without first adding
    push remotes.

    Args:
        local_repo: Path to the repo to push.
        user: The username to put on the push.
        repo_url: HTTPS url to the remote repo (without username/token!).
        branch: The branch to push to.
    """
    util.validate_types(
        local_repo=(local_repo, str),
        user=(user, str),
        repo_url=(repo_url, str),
        branch=(branch, str))
    util.validate_non_empty(
        local_repo=local_repo, user=user, repo_url=repo_url, branch=branch)

    loop = asyncio.get_event_loop()
    pt = Push(local_path=local_repo, repo_url=repo_url, branch=branch)
    task = loop.create_task(_push_async(pt, user))
    loop.run_until_complete(task)


def push(push_tuples: Iterable[Push], user: str) -> List[str]:
    """Push to all repos defined in push_tuples asynchronously. Amount of
    concurrent tasks is limited by CONCURRENT_TASKS.

    Args:
        push_tuples: Push namedtuples defining local and remote repos.
        user: The username to put in the push.

    Returns:
        urls to which pushes failed with PushFailedError. Other errors are only
        logged.
    """
    # TODO valdate push_tuples
    util.validate_types(user=(user, str))
    util.validate_non_empty(push_tuples=push_tuples, user=user)

    # urls can only be extracted from PushFailedErrors
    return [
        exc.url for exc in _batch_execution(_push_async, push_tuples, user)
        if isinstance(exc, PushFailedError)
    ]


def _batch_execution(
        batch_func: Callable[[Iterable[Any], Any], List[asyncio.Task]],
        arg_list: List[Any], *batch_func_args,
        **batch_func_kwargs) -> List[Exception]:
    """Take a batch function (any function whos first argument is an iterable)
    and send in send in CONCURRENT_TASKS amount of arguments from the arg_list
    until it is exhausted. The batch_func_kwargs are provided on each call.

    Args:
        batch_func: A function that takes an iterable as a first argument and returns
        a list of asyncio.Task objects.
        arg_list: A list of objects that are of the same type as the
        batch_func's first argument.
        batch_func_kwargs: Additional keyword arguments to the batch_func.

    Returns:
        a list of exceptions raised in the tasks returned by the batch function.
    """
    completed_tasks = []

    loop = asyncio.get_event_loop()
    for i in range(0, len(arg_list), CONCURRENT_TASKS):
        tasks = [
            loop.create_task(
                batch_func(list_arg, *batch_func_args, **batch_func_kwargs))
            for list_arg in arg_list[i:i + CONCURRENT_TASKS]
        ]
        loop.run_until_complete(asyncio.wait(tasks))
        completed_tasks += tasks

    exceptions = [
        task.exception() for task in completed_tasks if task.exception()
    ]
    for exc in exceptions:
        LOGGER.error(str(exc))

    return exceptions


def _execute_batch(
        create_task_func: Callable[[Iterable[Any], Any], asyncio.Task],
        list_args: Iterable[Any], *args, **kwargs) -> List[asyncio.Task]:
    """something"""
    assert len(list_args) <= CONCURRENT_TASKS
    tasks = [
        create_task_func(list_arg, *args, **kwargs) for list_arg in list_args
    ]
    asyncio.get_event_loop().run_until_complete(asyncio.wait(tasks))
    return tasks