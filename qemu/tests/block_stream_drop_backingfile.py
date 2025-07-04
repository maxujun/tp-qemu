import os
import re

from avocado.utils import process
from virttest import data_dir, error_context, storage, utils_misc


@error_context.context_aware
def run(test, params, env):
    """
    block_stream_without_backingfile test:
    1). bootup guest
    2). create snapshots chian(base->sn1->sn2), verify backingfile should sn1
    3). merge sn1 to sn2 (sn1->sn2) aka block stream with special base, after
        job done, then check backingfile is base and sn1 not opening by qemu
    4). merge base to sn2(base->sn2) after this step sn2 should no backingfile
        and sn1 and base should not opening by qemu
    5). reboot guest vierfy it works correctly
    6). verify not backingfile with qemu-img command too;

    :param test: Qemu test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    vm = env.get_vm(params["main_vm"])
    vm.verify_alive()
    timeout = int(params.get("login_timeout", 360))
    session = vm.wait_for_login(timeout=timeout)
    alive_check_cmd = params.get("alive_check_cmd", "dir")
    image_file = storage.get_image_filename(params, data_dir.get_data_dir())
    image_dir = os.path.dirname(image_file)
    qemu_img = utils_misc.get_qemu_img_binary(params)
    speed = int(params.get("limited_speed", 0))
    wait_timeout = int(params.get("wait_timeout", 3600))

    def wait_job_done(timeout=3600):
        """
        Wait for job on the device done, raise TestFail exception if timeout;
        """
        if (
            utils_misc.wait_for(
                lambda: not vm.monitor.query_block_job(device_id),
                timeout,
                first=0.2,
                step=2.0,
                text="Wait for canceling block job",
            )
            is None
        ):
            test.fail("Wait job finish timeout in %ss" % timeout)

    def verify_backingfile(expect_backingfile):
        """
        Got backingfile from monitor then verify it with expect_backingfile,
        if not raise TestFail exception;
        """
        backing_file = vm.monitor.get_backingfile(device_id)
        if backing_file != expect_backingfile:
            test.fail("Unexpect backingfile(%s)" % backing_file)

    def get_openingfiles():
        """
        Return files which opening by qemu process;
        """
        pid = vm.get_pid()
        cmd = params.get("snapshot_check_cmd") % pid
        return set(
            process.system_output(cmd, ignore_status=True, shell=True).splitlines()
        )

    snapshots = list(map(lambda x: os.path.join(image_dir, x), ["sn1", "sn2"]))
    try:
        error_context.context("Create snapshots-chain(base->sn1->sn2)", test.log.info)
        for index, snapshot in enumerate(snapshots):
            base_file = index and snapshots[index - 1] or image_file
            device_id = vm.live_snapshot(base_file, snapshot)
            if not device_id:
                test.fail("Fail to create %s" % snapshot)
        error_context.context("Check backing-file of sn2", test.log.info)
        verify_backingfile(snapshots[0])

        error_context.context("Merge sn1 to sn2", test.log.info)
        vm.monitor.block_stream(device_id, base=image_file, speed=speed)
        wait_job_done(wait_timeout)
        error_context.context("Check backing-file of sn2", test.log.info)
        verify_backingfile(image_file)
        error_context.context("Check sn1 is not opening by qemu process", test.log.info)
        if snapshots[0] in get_openingfiles():
            test.fail("sn1 (%s) is opening by qemu" % snapshots[0])

        error_context.context("Merge base to sn2", test.log.info)
        vm.monitor.block_stream(device_id)
        wait_job_done(wait_timeout)
        error_context.context("Check backing-file of sn2", test.log.info)
        verify_backingfile(None)
        error_context.context(
            "check sn1 and base are not opening by qemu process", test.log.info
        )
        if set([snapshots[0], image_file]).issubset(get_openingfiles()):
            test.fail("%s is opening by qemu" % set([snapshots[0], image_file]))
        error_context.context("Reboot VM to check it works fine", test.log.info)
        session = vm.reboot(session=session, timeout=timeout)
        session.cmd(alive_check_cmd)
        vm.destroy()
        error_context.context("Check backing-file of sn2 by qemu-img", test.log.info)
        cmd = "%s info %s" % (qemu_img, snapshots[1])
        if re.search(
            "backing file",
            process.system_output(cmd, ignore_status=True).decode("utf-8"),
        ):
            test.fail("should no backing-file in this step")
    finally:
        files = " ".join(snapshots)
        process.system(r"\rm -rf %s" % files)
