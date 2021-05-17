# Copyright 2021 Foundries.io
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import requests
import subprocess
import yaml
from conductor.celery import app as celery
from celery.utils.log import get_task_logger
from conductor.core.models import Run, Build, LAVADeviceType, LAVADevice, LAVAJob, Project
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.template.loader import get_template
from django.utils import timezone
from urllib.parse import urljoin

logger = get_task_logger(__name__)
DEFAULT_TIMEOUT = 30

translate_result = {
    "pass": "PASSED",
    "fail": "FAILED",
    "skip": "SKIPPED",
    "unknown": "SKIPPED"
}

def _get_os_tree_hash(url, project):
    logger.debug("Retrieving ostree hash with base url: %s" % url)
    # ToDo: add headers for authentication
    token = getattr(settings, "FIO_API_TOKEN", None)
    authentication = {
        "OSF-TOKEN": token,
    }
    os_tree_hash_request = requests.get(urljoin(url, "other/ostree.sha.txt"), headers=authentication)
    if os_tree_hash_request.status_code == 200:
        return os_tree_hash_request.text.strip()
    return None


@celery.task
def create_build_run(build_id, run_url, run_name, lava_job_type=LAVAJob.JOB_LAVA):
    logger.debug("Received task for build: %s" % build_id)
    build = None
    try:
        build = Build.objects.get(pk=build_id)
    except Build.DoesNotExist:
        return None
    device_type = None
    try:
        device_type = LAVADeviceType.objects.get(name=run_name, project=build.project)
    except LAVADeviceType.DoesNotExist:
        return None
    # compose LAVA job definitions for each device
    run, _ = Run.objects.get_or_create(
        build=build,
        device_type=device_type,
        ostree_hash=_get_os_tree_hash(run_url, build.project),
        run_name=run_name
    )
    context = {
        "device_type": run_name,
        "build_url": build.url,
        "build_id": build.build_id,

        "IMAGE_URL": "%slmp-factory-image-%s.wic.gz" % (run_url, run_name),
        "BOOTLOADER_URL": "%simx-boot-%s" % (run_url, run_name),
        "SPLIMG_URL": "%sSPL-%s" % (run_url, run_name),
        "prompts": ["fio@%s" % run_name, "Password:", "root@%s" % run_name],
        "net_interface": device_type.net_interface,
        "os_tree_hash": run.ostree_hash,
        "target": build.build_id,
    }
    dt_settings = device_type.get_settings()
    for key, value in dt_settings.items():
        try:
            context.update({key: value.format(run_url=run_url, run_name=run_name)})
        except KeyError:
            # ignore KeyError in case of misformatted string
            pass
        except AttributeError:
            # ignore values that are not strings
            pass
    template = get_template("lava_template.yaml")
    if lava_job_type == LAVAJob.JOB_OTA:
        template = get_template("lava_deploy_template.yaml")
    lava_job_definition = template.render(context)
    job_ids = build.project.submit_lava_job(lava_job_definition)
    logger.debug(job_ids)
    for job in job_ids:
        LAVAJob.objects.create(
            job_id=job,
            definition=lava_job_definition,
            project=build.project,
            job_type=lava_job_type,
        )
        if lava_job_type != LAVAJob.JOB_OTA:
            # only create OTA jobs for each 'regular' job
            create_ota_job(build_id, run_url, run_name)


@celery.task
def create_ota_job(build_id, run_url, run_name):
    # only do OTA from previous platform build
    # this is current limitation
    build = None
    try:
        build = Build.objects.get(pk=build_id)
    except Build.DoesNotExist:
        return None
    # find previous platform build
    previous_builds = build.project.build_set.filter(build_id__lt=build.build_id).order_by('-build_id')
    if previous_builds:
        previous_build = previous_builds[0]
        try:
            run = previous_build.run_set.get(run_name=run_name)
            run_url = f"{previous_build.url}runs/{run.run_name}/"
            create_build_run(previous_build.id, run_url, run.run_name, lava_job_type=LAVAJob.JOB_OTA)
        except Run.DoesNotExist:
            return None


@celery.task
def update_build_commit_id(build_id, run_url):
    token = getattr(settings, "FIO_API_TOKEN", None)
    authentication = {
        "OSF-TOKEN": token,
    }
    run_json_request = requests.get(urljoin(run_url, ".rundef.json"), headers=authentication)
    if run_json_request.status_code == 200:
        with transaction.atomic():
            build = None
            try:
                build = Build.objects.get(pk=build_id)
            except Build.DoesNotExist:
                return None

            run_json = run_json_request.json()
            commit_id = run_json['env']['GIT_SHA']
            build.commit_id = commit_id
            build.save()


class ProjectMisconfiguredError(Exception):
    pass


def __project_repository_exists(project):
    repository_path = os.path.join(settings.FIO_REPOSITORY_HOME, project.name)
    if os.path.exists(repository_path):
        if os.path.isdir(repository_path):
            # do nothing, directory exists
            return True
        else:
            # raise exception, there should not be a file with this name
            raise ProjectMisconfiguredError()
    return False


@celery.task
def create_project_repository(project_id):
    project = None
    try:
        project = Project.objects.get(pk=project_id)
    except Project.DoesNotExist:
        # do nothing if project is not found
        return
    # check if repository DIR already exists
    repository_path = os.path.join(settings.FIO_REPOSITORY_HOME, project.name)
    if not __project_repository_exists(project):
        # create repository DIR
        os.makedirs(repository_path)
    # call shell script to clone and configure repository
    cmd = [os.path.join(settings.FIO_REPOSITORY_SCRIPT_PATH_PREFIX, "checkout_repository.sh"),
           "-d", repository_path,
           "-r", settings.FIO_REPOSITORY_REMOTE_NAME,
           "-u", "%s/%s/lmp-manifest.git" % (settings.FIO_REPOSITORY_BASE, project.name),
           "-l", settings.FIO_BASE_REMOTE_NAME,
           "-w", settings.FIO_BASE_MANIFEST,
           "-t", settings.FIO_REPOSITORY_TOKEN]
    print(cmd)
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        pass


@celery.task
def merge_lmp_manifest():
    # merge LmP manifest into all project manifst repositories
    projects = Project.objects.all()
    for project in projects:
        repository_path = os.path.join(settings.FIO_REPOSITORY_HOME, project.name)
        if not __project_repository_exists(project):
            # ignore project with no repository
            continue
        # call shell script to merge manifests
        cmd = [os.path.join(settings.FIO_REPOSITORY_SCRIPT_PATH_PREFIX,"merge_manifest.sh"),
               "-d", repository_path,
               "-r", settings.FIO_REPOSITORY_REMOTE_NAME,
               "-l", settings.FIO_BASE_REMOTE_NAME]
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError:
            pass


#@celery.task
#def check_build_release():
#    # this task should run once or twice a day
#    # it fills in Build.is_release field based on the repository tags
#    pass


@celery.task
def device_pdu_action(device_id, power_on=True):
    lava_device = None
    try:
        lava_device = LAVADevice.objects.get(pk=device_id)
    except LAVADevice.DoesNotExist:
        return
    # get device dictionary
    device_dict_url = urljoin(lava_device.project.lava_url, f"devices/{lava_device.name}/dictionary?render=true")
    auth = {
        "Authorization": f"Token {lava_device.project.lava_api_token}"
    }
    device_request = requests.get(device_dict_url, headers=auth)
    device_dict = None
    if device_request.status_code == 200:
        device_dict = yaml.load(device_request.text, Loader=yaml.SafeLoader)
    # extract power on/off command(s)
    cmds = device_dict['commands']['power_on']
    logger.debug("Commands to be sent")
    logger.debug(cmds)
    if not power_on:
        cmds = device_dict['commands']['power_off']
    if not isinstance(cmds, list):
        cmds = [cmds]
    # use PDUAgent to run command(s) remotely
    if lava_device.pduagent:
        for cmd in cmds:
            lava_device.pduagent.message = cmd
            lava_device.pduagent.save()


def __get_testjob_results__(device, job_id):
    logger.debug(f"Retrieving result summary for job: {job_id}")
    current_target = device.get_current_target()
    target_name = current_target.get('target-name')
    lava_job_results = {}
    authentication = {
        "Authorization": "Token %s" % device.project.lava_api_token,
    }
    # get job definition
    definition_resp = requests.get(
        urljoin(device.project.lava_url, f"jobs/{job_id}/"),
        headers=authentication,
        timeout=DEFAULT_TIMEOUT
    )
    job_definition = None
    expected_test_list = []
    if definition_resp.status_code == 200:
        job_json = definition_resp.json()
        job_definition = yaml.load(job_json["definition"], Loader=yaml.SafeLoader)
        # todo: check if tests exist and they come with definitions
        # this is only correct for some test jobs
        for action in job_definition['actions']:
            if 'test' in action.keys():
                for expected_test in action['test']['definitions']:
                    expected_test_list.append(expected_test['name'])

    # compare job definition with results (any missing)?
    suites_resp = requests.get(
        urljoin(device.project.lava_url, f"jobs/{job_id}/suites/"),
        headers=authentication,
        timeout=DEFAULT_TIMEOUT
    )
    while suites_resp.status_code == 200:
        suites_content = suites_resp.json()
        for suite in suites_content['results']:
            if suite['name'] != 'lava':
                index, suite_name = suite['name'].split("_", 1)
                try:
                    expected_test_list.remove(suite_name)
                except ValueError:
                    logger.error(f"Suite {suite_name} not found in expected list")
                lava_job_results[suite_name] = {
                    "name": suite_name,
                    "status": "PASSED",
                    "target-name": target_name,
                    "results": []
                }
                tests_resp = requests.get(
                    urljoin(device.project.lava_url, f"jobs/{job_id}/suites/{suite['id']}/tests"),
                    headers=authentication,
                    timeout=DEFAULT_TIMEOUT
                )
                while tests_resp.status_code == 200:
                    tests_content = tests_resp.json()
                    for test_result in tests_content['results']:
                        #metadata = yaml.load(test_result['metadata'], Loader=yaml.SafeLoader)
                        lava_job_results[suite_name]['results'].append(
                            {
                                "name": test_result['name'],
                                "status": translate_result[test_result['result']],
                                "local_ts": 0
                            }
                        )

                    #lava_job_results[suite_name]['tests'] = lava_job_results[suite_name]['tests'] + tests_content['results']
                    if tests_content['next']:
                        tests_resp = requests.get(
                            tests_content['next'],
                            headers=authentication,
                            timeout=DEFAULT_TIMEOUT
                        )
                    else:
                        break
        if suites_content['next']:
            suites_resp = requests.get(
                suites_content['next'],
                headers=authentication,
                timeout=DEFAULT_TIMEOUT
            )
        else:
            break

    return lava_job_results


#def test_res(device, job_id):
#    return __get_testjob_results__(device, job_id)

@celery.task
def retrieve_lava_results(device_id, job_id):
    lava_db_device = None
    try:
        lava_db_device = LAVADevice.objects.get(pk=device_id)
    except LAVADevice.DoesNotExist:
        logger.debug(f"Device with ID {device_id} not found")
        return
    lava_results = __get_testjob_results__(lava_db_device, job_id)
    for suite_name, result in lava_results.items():
        __report_test_result(lava_db_device, result)


@celery.task
def process_testjob_notification(event_data):
    job_id = event_data.get("job")
    try:
        lava_job = LAVAJob.objects.get(job_id=job_id)
        device_name = event_data.get("device")
        lava_db_device = None
        logger.debug(f"Processing job: {job_id}")
        logger.debug(f"LAVA device name: {device_name}")
        if device_name:
            lava_db_device = LAVADevice.objects.get(name=device_name, project=lava_job.project)
            lava_job.device = lava_db_device
            lava_job.save()
            logger.debug(f"LAVA device is: {lava_db_device.id}")
        if lava_job.job_type == LAVAJob.JOB_OTA and \
                event_data.get("state") == "Running" and \
                lava_db_device:
            lava_db_device.request_maintenance()
        if lava_job.job_type == LAVAJob.JOB_OTA and \
                event_data.get("state") == "Finished" and \
                lava_db_device:
            if event_data.get("health") == "Complete":
                # remove device from factory at the latest possible moment
                lava_db_device.remove_from_factory()
                device_pdu_action(lava_db_device.id, power_on=True)
            else:
                # report OTA failure?
                lava_db_device.request_online()
                logger.error("OTA flashing job failed!")
        if lava_job.job_type == LAVAJob.JOB_LAVA and \
                event_data.get("state") == "Running" and \
                lava_db_device:
            # remove device from factory so it can autoregister
            # and update it's target ID
            lava_db_device.remove_from_factory()
        if lava_job.job_type == LAVAJob.JOB_LAVA and \
                event_data.get("state") == "Finished" and \
                lava_db_device:
            retrieve_lava_results(lava_db_device.id, job_id)

    except LAVAJob.DoesNotExist:
        logger.debug(f"Job {job_id} not found")
        return
    except LAVADevice.DoesNotExist:
        logger.debug(f"Device from job {job_id} not found")
        return


@celery.task
def process_device_notification(event_data):
    pass


def __report_test_result(device, result):
    token = getattr(settings, "FIO_API_TOKEN", None)
    authentication = {
        "OSF-TOKEN": token,
    }

    url = f"https://api.foundries.io/ota/devices/{device.project.name}-{device.name}/tests/"
    test_dict = result.copy()
    test_dict.pop("status")
    new_test_request = requests.post(url, json=test_dict, headers=authentication)
    logger.info(f"Reporting test {result['name']} for {device.name}")
    if new_test_request.status_code == 201:
        test_details = new_test_request.json()
        result.update(test_details)
        details_url = f"{url}{test_details['test-id']}"
        update_details_request = requests.put(details_url, json=result, headers=authentication)
        if update_details_request.status_code == 200:
            logger.debug(f"Successfully reported details for {test_details['test-id']}")
        else:
            logger.warning(f"Failed to report details for {test_details['test-id']}")
    else:
        logger.warning(f"Failed to create test result for {device.project.name}-{device.name}")
        logger.warning(new_test_request.text)


@celery.task
def report_test_results(lava_device_id, target_name, ota_update_result=None, ota_update_from=None, result_dict=None):
    device = None
    try:
        device = LAVADevice.objects.get(pk=lava_device_id)
    except LAVADevice.DoesNotExist:
        logger.error(f"Device with ID: {lava_device_id} not found!")
        return
    if ota_update_result != None:
        test_name = f"ota_update_from_{ota_update_from}"
        test_result = "PASSED"
        if not ota_update_result:
            test_result = "FAILED"
        result = {
            "name": test_name,
            "status": test_result,
            "target-name": target_name
        }
        __report_test_result(device, result)
    elif result_dict != None:
        __report_test_result(device, result_dict)


@celery.task
def check_ota_completed():
    # This is a periodic task which checks all devices which are in
    # OTA configuration. The default timeout for performing OTA and
    # running all tests is 30 minutes. If the device is not updated
    # after this timeout OTA is considered to be unsuccessful. The
    # device is moved back under LAVA control.
    deadline = timezone.now() - timedelta(minutes=30)
    devices = LAVADevice.objects.filter(
        controlled_by=LAVADevice.CONTROL_PDU,
        ota_started__lt=deadline
    )
    for device in devices:
        current_target = device.get_current_target()
        # determine whether current target is correct
        last_build = device.project.build_set.last()
        previous_builds = last_build.project.build_set.filter(build_id__lt=last_build.build_id).order_by('-build_id')
        previous_build = None
        if previous_builds:
            previous_build = previous_builds[0]
        try:
            last_run = last_build.run_set.get(run_name=device.device_type.name)
            target_name = current_target.get('target-name')
            if current_target.get('ostree-hash') == last_run.ostree_hash:
                # update successful
                logger.info(f"Device {device.name} successfully updated to {last_build.build_id}")
                report_test_results(device.id, target_name, ota_update_result=True, ota_update_from=previous_build.build_id)
            else:
                logger.info(f"Device {device.name} NOT updated to {last_build.build_id}")
                report_test_results(device.id, target_name, ota_update_result=False, ota_update_from=previous_build.build_id)

            # switch the device to LAVA control
            device.request_online()
            device.controlled_by = LAVADevice.CONTROL_LAVA
            device.save()
            device_pdu_action(device.id, power_on=False)
        except Run.DoesNotExist:
            logger.error(f"Run {device.device_type.name} for build {last_build.id} does not exist")
