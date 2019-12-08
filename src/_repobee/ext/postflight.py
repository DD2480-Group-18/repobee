"""This is a test plugin for the clone_task hook. It serves no real purpose.

.. module:: postflight
    :synopsis: Test plugin for the clone_task hook.

.. moduleauthor:: Simon Larsén
"""

import pathlib
import repobee_plug as plug


def callback(path: pathlib.Path, api: plug.API):
    return plug.HookResult(
        hook="postflight",
        msg="Successful postflight on {}".format(path),
        status=plug.Status.SUCCESS,
    )


@plug.repobee_hook
def clone_task() -> plug.Task:
    return plug.Task(callback=callback)
