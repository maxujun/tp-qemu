import os
import re

from virttest import cpu, error_context, utils_misc, utils_test


@error_context.context_aware
def run(test, params, env):
    """
    Runs CPU hotplug test:

    1) Boot the vm with -smp X,maxcpus=Y
    2) After logged into the vm, check CPUs number
    3) Send the monitor command cpu_set [cpu id] for each cpu we wish to have
    4) Verify if guest has the additional CPUs showing up
    5) reboot the vm
    6) recheck guest get hot-pluged CPUs
    7) Try to bring them online by writing 1 to the 'online' file inside
       that dir(Linux guest only)
    8) Run the CPU Hotplug test suite shipped with autotest inside guest
       (Linux guest only)

    :param test: QEMU test object.
    :param params: Dictionary with test parameters.
    :param env: Dictionary with the test environment.
    """
    error_context.context(
        "boot the vm, with '-smp X,maxcpus=Y' option,thus allow hotplug vcpu",
        test.log.info,
    )
    vm = env.get_vm(params["main_vm"])
    vm.verify_alive()

    timeout = int(params.get("login_timeout", 360))
    session = vm.wait_for_login(timeout=timeout)

    n_cpus_add = int(params.get("n_cpus_add", 1))
    maxcpus = int(params.get("maxcpus", 160))
    current_cpus = int(params.get("smp", 1))
    onoff_iterations = int(params.get("onoff_iterations", 20))
    cpu_hotplug_cmd = params.get("cpu_hotplug_cmd", "")

    if n_cpus_add + current_cpus > maxcpus:
        test.log.warning("CPU quantity more than maxcpus, set it to %s", maxcpus)
        total_cpus = maxcpus
    else:
        total_cpus = current_cpus + n_cpus_add

    error_context.context(
        "check if CPUs in guest matches qemu cmd before hot-plug", test.log.info
    )
    if not cpu.check_if_vm_vcpu_match(current_cpus, vm):
        test.error("CPU quantity mismatch cmd before hotplug !")

    for cpuid in range(current_cpus, total_cpus):
        error_context.context("hot-pluging vCPU %s" % cpuid, test.log.info)
        vm.hotplug_vcpu(cpu_id=cpuid, plug_command=cpu_hotplug_cmd)

    output = vm.monitor.send_args_cmd("info cpus")
    test.log.debug("Output of info CPUs:\n%s", output)

    cpu_regexp = re.compile(r"CPU #(\d+)")
    total_cpus_monitor = len(cpu_regexp.findall(output))
    if total_cpus_monitor != total_cpus:
        test.fail(
            "Monitor reports %s CPUs, when VM should have"
            " %s" % (total_cpus_monitor, total_cpus)
        )
    # Windows is a little bit lazy that needs more secs to recognize.
    error_context.context(
        "hotplugging finished, let's wait a few sec and check CPUs quantity in guest.",
        test.log.info,
    )
    if not utils_misc.wait_for(
        lambda: cpu.check_if_vm_vcpu_match(total_cpus, vm),
        60 + total_cpus,
        first=10,
        step=5.0,
        text="retry later",
    ):
        test.fail("CPU quantity mismatch cmd after hotplug !")
    error_context.context("rebooting the vm and check CPU quantity !", test.log.info)
    session = vm.reboot()
    if not cpu.check_if_vm_vcpu_match(total_cpus, vm):
        test.fail("CPU quantity mismatch cmd after hotplug and reboot !")

    # Window guest doesn't support online/offline test
    if params["os_type"] == "windows":
        return

    error_context.context("locating online files for guest's new CPUs")
    r_cmd = "find /sys/devices/system/cpu/cpu*/online -maxdepth 0 -type f"
    online_files = session.cmd(r_cmd)
    # Sometimes the return value include command line itself
    if "find" in online_files:
        online_files = " ".join(online_files.strip().split("\n")[1:])
    test.log.debug("CPU online files detected: %s", online_files)
    online_files = online_files.split()
    online_files.sort()

    if not online_files:
        test.fail("Could not find CPUs that can be enabled/disabled on guest")

    control_path = os.path.join(test.virtdir, "control", "cpu_hotplug.control")

    timeout = int(params.get("cpu_hotplug_timeout", 300))
    error_context.context("running cpu_hotplug autotest after cpu addition")
    utils_test.run_autotest(vm, session, control_path, timeout, test.outputdir, params)

    # Last, but not least, let's offline/online the CPUs in the guest
    # several times
    irq = 15
    irq_mask = "f0"
    for i in range(onoff_iterations):
        session.cmd("echo %s > /proc/irq/%s/smp_affinity" % (irq_mask, irq))
        for online_file in online_files:
            session.cmd("echo 0 > %s" % online_file)
        for online_file in online_files:
            session.cmd("echo 1 > %s" % online_file)
