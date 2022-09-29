import sys
import os
from kraken.sshv import utils as utils
from kraken.storage import action as action
import gevent
import threading
import time
import ctypes
import inspect
import telnetlib
import yaml
from kraken.sshv import send_email

def _async_raise(tid, exctype):
    """raises the exception, performs cleanup if needed"""
    tid = ctypes.c_long(tid)
    if not inspect.isclass(exctype):
        exctype = type(exctype)
    res = ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, ctypes.py_object(exctype))
    if res == 0:
        print("invalid thread id")
        # raise ValueError("invalid thread id")
    elif res != 1:
        # """if it returns a number greater than one, you're in trouble,
        # and you should call it again with exc=NULL to revert the effect"""
        ctypes.pythonapi.PyThreadState_SetAsyncExc(tid, None)
        print("PyThreadState_SetAsyncExc failed")
        # raise SystemError("PyThreadState_SetAsyncExc failed")


def stop_thread(thread):
    _async_raise(thread.ident, SystemExit)



def check_crm_node_status(result, config, have_down):

    err = False
    if result:
        host_list = config.get_hostname()
        re_online = "Online:(.*?)]"
        online_result = utils.re_findall(re_online, result)
        if not online_result:
            utils.prt_log('', f"CRM Online status error ", 1)
            err = True
        re_offline = "OFFLINE:(.*?)]"
        offline_result = utils.re_findall(re_offline, result)

        if have_down:
            shutdown_node = config.get_down_node()["hostname"]
            if not offline_result:
                utils.prt_log('', f"CRM OFFLINE status error ", 1)
                err = True
            if not err:          
                if not shutdown_node in offline_result[0]:
                    utils.prt_log('', f"CRM {shutdown_node} OFFLINE status error ", 1)
                    err = True
            host_list = config.get_up_hostname()

        for host in host_list:
            if not host in online_result[0]:
                utils.prt_log('', f"CRM {host} Online status error ", 1)
                err = True
                
        return err


def get_crm_status_by_type(result, resource, type):
    if result:
        if type in ['IPaddr2', 'iSCSITarget', 'portblock', 'iSCSILogicalUnit']:
            re_string = f'{resource}\s*\(ocf::heartbeat:{type}\):\s*(\w*)\s*(\w*)?'
            re_result = utils.re_search(re_string, result, "groups")
            return re_result
        if type == 'FailedActions':
            re_string = "Failed Actions:\s*.*\*\s\w*\son\s(\S*)\s'(.*)'\s.*exitreason='(.*)',\s*.*"
            re_result = utils.re_search(re_string, result, "group")
            return re_result
        if type == 'AllLUN':
            re_string = "Stopped:(.*?)]"
            re_result = utils.re_findall(re_string, result)
            return re_result
        # if type == 'AllLUN':
        #     re_string = f'(\S+)\s*\(ocf::heartbeat:iSCSILogicalUnit\):\s*(\w*)\s*(\w*)?'
        #     re_result = utils.re_findall(re_string, result)
        #     return re_result


def ckeck_drbd_status_error(result, resource):
    re_stand_alone = f'connection:StandAlone'
    re_string = f'{resource}\s*role:(\w+).*\s*disk:(\w+)'
    # re_peer_string = '\S+\s*role:(\w+).*\s*peer-disk:(\w+)'
    if result:
        re_stand_alone_result = utils.re_search(re_stand_alone, result, "bool")
        if re_stand_alone_result:
            return 'StandAlone'
        re_result = utils.re_search(re_string, result, "groups")
        return re_result


def check_drbd_conns_status(result):
    re_string = r'([a-zA-Z0-9_-]+)\s*\|\s*([a-zA-Z0-9-]+).*\d+\s*\|\s*[a-zA-Z]*\s*\|\s*([a-zA-Z0-9()]*)\s*\|\s*([a-zA-Z]*)\s*\|'
    if result:
        re_result = utils.re_findall(re_string, result)
        return re_result






class Connect(object):
    """
    通过ssh连接节点，生成连接对象的列表
    """
    list_vplx_ssh = []
    list_normal_vplx_ssh = []
    down_node_ssh = None

    def __new__(cls, *args, **kwargs):
        if not hasattr(cls, '_instance'):
            Connect._instance = super().__new__(cls)
            Connect._instance.config = args[0]
            Connect.get_ssh_conn(Connect._instance)
        return Connect._instance

    def get_ssh_conn(self):
        local_ip = utils.get_host_ip()
        vplx_configs = self.config.get_vplx_configs()
        down_ip = self.config.get_down_node()["ip"]
        username = "root"
        for vplx_config in vplx_configs:
            if "username" in vplx_config.keys():
                if vplx_config['username'] is not None:
                    username = vplx_config['username']

            if local_ip == vplx_config['public_ip']:
                self.list_vplx_ssh.append(None)
                self.list_normal_vplx_ssh.append(None)
                utils.set_global_dict_value(None, vplx_config['public_ip'])
            else:

                ssh_conn = utils.SSHConn(vplx_config['public_ip'], vplx_config['port'], username,
                                            vplx_config['password'])
                self.list_vplx_ssh.append(ssh_conn)
                if vplx_config['public_ip'] != down_ip:
                    self.list_normal_vplx_ssh.append(ssh_conn)
                utils.set_global_dict_value(ssh_conn, vplx_config['public_ip'])

    def try_ssh_for_downnode(self):
        vplx_configs = self.config.get_vplx_configs()
        down_ip = self.config.get_down_node()["ip"]
        for vplx_config in vplx_configs:
            if vplx_config['public_ip'] == down_ip:
                self.down_node_ssh = utils.SSHConn(vplx_config['public_ip'], vplx_config['port'], "root",
                                            vplx_config['password'],timeout=300)

        utils.prt_log('', f"The downed node has been on", 0)

class ConnectNormal(object):
    """
    通过ssh连接节点，生成连接对象的列表
    """
    list_nodes_ssh = []

    def __new__(cls, *args, **kwargs):
        if not hasattr(cls, '_instance'):
            ConnectNormal._instance = super().__new__(cls)
            ConnectNormal._instance.config = args[0]
            ConnectNormal.get_ssh_conn(ConnectNormal._instance)
        return ConnectNormal._instance

    def get_ssh_conn(self):
        local_ip = utils.get_host_ip()
        nodes_configs = self.config.get_nodes_configs()
        username = "root"
        for node_config in nodes_configs:
            if "username" in node_config.keys():
                if node_config['username'] is not None:
                    username = node_config['username']

            if local_ip == node_config['public_ip']:
                self.list_nodes_ssh.append(None)
                utils.set_global_dict_value(None, node_config['public_ip'])
            else:

                ssh_conn = utils.SSHConn(node_config['public_ip'], node_config['port'], username,
                                            node_config['password'])
                self.list_nodes_ssh.append(ssh_conn)
                utils.set_global_dict_value(ssh_conn, node_config['public_ip'])

class TELConn(object):

    def __init__(self, ip):
        try:
            self.tel = telnetlib.Telnet(ip, port = 23, timeout = 10)
        except Exception as E:
            utils.prt_log('', f"Telnet connect failed", 2)


    def down_port(self,port):# time 12s
        self.tel.write(b"configure terminal\n")
        time.sleep(2)
        self.tel.write(b"enable\n")
        time.sleep(2)
        cmd = "interface %s\n" % port
        cmd = bytes(cmd, encoding='utf-8')
        self.tel.write(cmd)
        time.sleep(2)
        self.tel.write(b"shutdown\n")
        time.sleep(2)
        utils.prt_log('', f"Shutdown switch interface {port}", 0)


    def nodown_port(self,port):# time 12s
        self.tel.write(b"configure terminal\n")
        time.sleep(2)
        self.tel.write(b"enable\n")
        time.sleep(2)
        cmd = "interface %s\n" % port
        cmd = bytes(cmd, encoding='utf-8')
        self.tel.write(cmd)
        time.sleep(2)
        self.tel.write(b"no shutdown\n")
        time.sleep(2)
        utils.prt_log('', f"Open switch interface {port}", 0)

    def check_port(self,port):# time 12s

        self.tel.write(b"configure terminal\n")
        time.sleep(2)
        self.tel.write(b"enable\n")
        time.sleep(2)
        cmd = "interface %s\n" % port
        cmd = bytes(cmd, encoding='utf-8')
        self.tel.write(cmd)
        time.sleep(2)
        self.tel.write(b"show this\n")
        info = self.tel.read_all()
        print(21233)
        print(info)
        if info:
            if "shutdown" in info:
                return 1
            else:
                return 0

class IscsiTest(object):
    def __init__(self, config):
        self.config = config
        self.conn = Connect(self.config)
        self.vplx_configs = self.config.get_vplx_configs()
        self.node_list = [vplx_config["hostname"] for vplx_config in self.vplx_configs]
        self.lun_list = []
        self.clean_dmesg()
        self.crm_start_time = time.strftime("%Y/%m/%d %H:%M:%S", time.localtime())

    def test_drbd_in_used(self):
        with open(sys.path[0] + '/kraken/scenarios/spof_pvc_scenario.yaml', 'r', encoding='utf-8') as sps:
            data = yaml.full_load(sps)
            mail_receiver = data.get['mail_receive']
        start_time = time.strftime("%Y/%m/%d %H:%M:%S", time.localtime())
        if len(self.conn.list_vplx_ssh) != 3:
            utils.prt_log('', f"Please make sure there are three nodes for this test", 2)
            send_email.STMPEmail(mail_receiver,
                                 message2='VersaTST test interrupted，resource status is not passed, exit').send_fail()
        test_times = self.config.get_test_times()
        device = self.config.get_device()
        target = self.config.get_target()
        resource = self.config.get_resource()
        ip_obj = action.IpService(self.conn.list_vplx_ssh[0])
        ip_node = utils.get_global_dict_value(self.conn.list_vplx_ssh[0])
        for i in range(test_times):
            i = i + 1
            utils.set_times(i)
            print(f"Number of test times --- {i}")
            if not self.check_target_lun_status(target, resource,
                                                self.conn.list_vplx_ssh[0]):
                self.collect_crm_report_file(start_time, self.conn.list_vplx_ssh[0])
                utils.prt_log(self.conn.list_vplx_ssh[0], f"Finished to collect crm_report and exit testing ...", 2)
            if not self.ckeck_drbd_status(resource):
                self.collect_crm_report_file(start_time, self.conn.list_vplx_ssh[0])
                utils.prt_log(self.conn.list_vplx_ssh[0], f"Finished to collect crm_report and exit testing ...", 2)
            utils.prt_log(self.conn.list_vplx_ssh[0], f"Down {device} on {ip_node} ...", 0)
            ip_obj.down_device(device)
            time.sleep(40)
            if not self.check_target_lun_status(target, resource, self.conn.list_vplx_ssh[1]):
                ip_obj.up_device(device)
                ip_obj.netplan_apply()
                time.sleep(30)
                self.collect_crm_report_file(start_time, self.conn.list_vplx_ssh[0])
                utils.prt_log(self.conn.list_vplx_ssh[0], f"Finished to collect crm_report and exit testing ...", 2)
            utils.prt_log(self.conn.list_vplx_ssh[0], f"Up {device} on {ip_node} ...", 0)
            ip_obj.up_device(device)
            ip_obj.netplan_apply()
            time.sleep(30)
            if not self.ckeck_drbd_status(resource):
                self.collect_crm_report_file(start_time, self.conn.list_vplx_ssh[0])
                utils.prt_log(self.conn.list_vplx_ssh[0], f"Finished to collect crm_report and exit testing ...", 2)
            self.restore_resource(resource)
            if i == 1:
                self.collect_crm_report_file(start_time, self.conn.list_vplx_ssh[0])
                utils.prt_log(self.conn.list_vplx_ssh[0], f"Finished to collect crm_report", 0)
            utils.prt_log('', f"Wait 2 minutes to restore the original environment", 0)
            time.sleep(120)

    def check_drbd_crm_res(self, resource, have_down):
        flag = True
        tips = ''
        err = False
        conn = self.conn.list_normal_vplx_ssh[0]
        iscsi_obj = action.Iscsi(conn)

        crm_status = iscsi_obj.get_crm_status()
        print("crm status:")
        print(crm_status)
        if not crm_status:
            utils.prt_log(conn, f"Connection to crm cluster failed: Transport endpoint is not connected", 1)
            err = True
        else:
            err = check_crm_node_status(crm_status, self.config, have_down)

            # error_message = get_crm_status_by_type(crm_status, None, "FailedActions")
            # if error_message:
            #     utils.prt_log('', error_message, 1)
            #     err = True

            all_resource_status = get_crm_status_by_type(crm_status, None, "AllLUN")
            if all_resource_status:
                down_hostname = self.config.get_down_node()["hostname"]
                for status in all_resource_status:
                    if not down_hostname in status:
                        utils.prt_log(conn, f"crm status is abnormal:{crm_status} ", 1)
                        err = True

        if err:
            utils.prt_log(conn, f"Crm status is abnormal :", 1)
            utils.prt_log(conn, crm_status, 1)
            self.get_log(have_down)
        return err

    def move_back_crm_res(self,crm_location_node):
        err = 1
        move_time = 60
        conn = self.conn.list_normal_vplx_ssh[0]
        now_location_node = self.get_glinstor_location_node()
        if crm_location_node == now_location_node:
            return 0
        else:
            iscsi_obj = action.Iscsi(conn)
            iscsi_obj.move_res("g_linstor", crm_location_node)
            time.sleep(2)
            while(move_time):
                result = iscsi_obj.get_res_status("g_linstor")
                if crm_location_node in result:
                    err = 0
                    break
                move_time = move_time - 1
                time.sleep(1)
        return err

    def get_glinstor_location_node(self):

        glinstor_location_node = ''
        conn = self.conn.list_normal_vplx_ssh[0]
        iscsi_obj = action.Iscsi(conn)
        hostname_list = self.config.get_hostname()
        glinstor_info = iscsi_obj.get_glinstor_location()
        for hostname in hostname_list:
            if hostname in glinstor_info:
                glinstor_location_node = hostname
                break

        return glinstor_location_node

    def check_target_lun_status(self, target, resource, conn):
        flag = True
        tips = ''
        iscsi_obj = action.Iscsi(conn)
        crm_status = iscsi_obj.get_crm_status()

        error_message = get_crm_status_by_type(crm_status, None, "FailedActions")
        if error_message:
            print(error_message)
            return False
        init_target_status = get_crm_status_by_type(crm_status, target, "iSCSITarget")
        if init_target_status:
            if init_target_status[0] != 'Started':
                utils.prt_log(conn, f"Target status is {init_target_status[0]}", 1)
                return False
        else:
            utils.prt_log(conn, f"Can't get status of target {target}", 1)
            return False
        all_resource_status = get_crm_status_by_type(crm_status, None, "AllLUN")
        if all_resource_status:
            self.lun_list.clear()
            for status in all_resource_status:
                self.lun_list.append(status[0])
                if resource == status[0]:
                    tips = '* '
                    if not init_target_status[1] == status[2]:
                        utils.prt_log(conn, f"Target and LUN is not started on the same node", 1)
                        flag = False
                if status[1] != 'Started':
                    utils.prt_log(conn, f"{tips}{status[0]} status is {status[1]}", 1)
                    flag = False
            if not flag:
                return False
        else:
            utils.prt_log(conn, f"Can't get crm status", 1)
            return False
        return True

    def down_node(self):
        down_ip = self.config.get_down_node()["ip"]
        for ssh in self.conn.list_vplx_ssh:
            if utils.get_global_dict_value(ssh) == down_ip:
                ssh.down_self()

    def power_node_ipmi(self):
        down_info = self.config.get_down_node()
        bmc_ip = down_info["bmc_ip"]
        user = down_info["user"]
        bmc_password = down_info["bmc_password"]


        cmd = f"ipmitool -I lanplus -H {bmc_ip} -U {user} -P {bmc_password} power on"
        result = os.system(cmd)
        if not result:
            utils.prt_log(None, "Node has been power on... ",0)
        else:
            utils.prt_log(None, "ipmi power node failed!!!",0)



    def down_node_ipmi(self):
        down_info = self.config.get_down_node()
        bmc_ip = down_info["bmc_ip"]
        user = down_info["user"]
        bmc_password = down_info["bmc_password"]

        cmd = f"ipmitool -I lanplus -H {bmc_ip} -U {user} -P {bmc_password} power off"
        result = os.system(cmd)
        print(result)
        if not result:
            utils.prt_log(None, "Node has been down... ",0)

        else:
            utils.prt_log(None, "ipmi down node failed!!!",0)
     

    def check_if_on(self,kind):
        if kind == "node_down":
            self.conn.try_ssh_for_downnode()

    def change_node_interface(self,on):
        interface_ip = self.config.get_interface_inf()["ip"]
        interface = self.config.get_interface_inf()["interface"]
        for ssh in self.conn.list_vplx_ssh:
            if utils.get_global_dict_value(ssh) == interface_ip:
                if on:
                    ssh.up_interface(interface)
                else:   
                    ssh.down_interface(interface)

    def check_node_interface(self,if_on):
        err = 1
        interface_ip = self.config.get_interface_inf()["ip"]
        interface = self.config.get_interface_inf()["interface"]
        for ssh in self.conn.list_vplx_ssh:
            if utils.get_global_dict_value(ssh) == interface_ip:
                result = ssh.check_interface(interface)
                if if_on:
                    if not result:
                        utils.prt_log(None, "interface %s is on... "% interface,0)
                        err = 0
                else:
                    if result == 1:
                        utils.prt_log(None, "interface %s has been down... "% interface,0)
                        err = 0

        return err

    def check_switch_port(self,if_on):
        err = 1
        ip = self.config.get_switch_port()["ip"]
        port = self.config.get_switch_port()["port"]
        switch = TELConn(ip)
        result = switch.check_port(port)
        if if_on:
            if not result:
                utils.prt_log(None, "Port %s is on... "% port,0)
                err = 0
        else:
            if result == 1:
                utils.prt_log(None, "Port %s has been down... "% port,0)
                err = 0

        return err


    def change_switch_port(self,on):
        ip = self.config.get_switch_port()["ip"]
        port = self.config.get_switch_port()["port"]
        switch = TELConn(ip)
        if on:
            switch.nodown_port(port)
        else:
            switch.down_port(port)




    def ckeck_drbd_status_spof(self, resource, have_down):
        err = False
        if not have_down:
            result = self.ckeck_drbd_status(resource)
        else:
            result = 0
            stor_obj = action.Stor(self.conn.list_normal_vplx_ssh[0])
            if self.lun_list:
                all_lun_string = " ".join(self.lun_list)
            else:
                all_lun_string = resource
            times = 7
            while(times):

                resource_status_result = stor_obj.get_linstor_res(all_lun_string)
                resource_status = check_drbd_conns_status(resource_status_result)
                if resource_status:
                    break
                time.sleep(1)
                times = times - 1
            if resource_status:
                down_hostname = self.config.get_down_node()["hostname"]
                for status in resource_status:
                    if not down_hostname == status[1]:
                        if not "Connecting" in status[2]:
                            
                            utils.prt_log(self.conn.list_normal_vplx_ssh[0], f"Node {status[1]} resource {status[0]} connection is {status[2]}", 1)
                            result = 1
                        if status[3] != "UpToDate" and status[3] != "Diskless" and status[3] != "TieBreaker":
                            if "SyncTarget" in status[3]:
                                result = 2
                            else:
                                utils.prt_log(self.conn.list_normal_vplx_ssh[0], f"Node {status[1]} resource {status[0]} status is {status[3]}", 1)
                                result = 1


            else:
                err = True
                utils.prt_log('', f"Failed to connect to linstor after down node", 1)
        if result:
            err = True
            self.get_log(have_down)
        return err


    def ckeck_drbd_status(self, resource):
        flag = 0
        stor_obj = action.Stor(self.conn.list_normal_vplx_ssh[0])
        if self.lun_list:
            all_lun_string = " ".join(self.lun_list)
        else:
            all_lun_string = resource

        times = 7
        while(times):
            resource_status_result = stor_obj.get_linstor_res(all_lun_string)
            resource_status = check_drbd_conns_status(resource_status_result)
            if resource_status:
                break
            time.sleep(1)
            times = times - 1
        if resource_status:
            for status in resource_status:
                if status[2] != "Ok":
                    utils.prt_log(self.conn.list_normal_vplx_ssh[0], f"Node {status[1]} resource {status[0]} connection is {status[2]}", 1)
                    flag = 2
                if status[3] != "UpToDate" and status[3] != "Diskless":
                    if "SyncTarget" in status[3]:
                        flag = 1
                    else:
                        utils.prt_log(self.conn.list_normal_vplx_ssh[0], f"Node {status[1]} resource {status[0]} status is {status[3]}", 1)
                        flag = 2
        else:
            flag = 2
            utils.prt_log('',f"Failed to connect to linstor",1)
        return flag

    def restore_resource(self, resource):
        conn = self.conn.list_vplx_ssh[1]
        init_start_node = self.node_list[0]
        iscsi_obj = action.Iscsi(conn)
        iscsi_obj.ref_res()
        time.sleep(10)
        utils.prt_log(conn, f"Move {resource} back to {init_start_node} ...", 0)
        iscsi_obj.move_res(resource, init_start_node)
        time.sleep(20)
        crm_status = iscsi_obj.get_crm_status()
        resource_status = get_crm_status_by_type(crm_status, resource, "iSCSILogicalUnit")
        if resource_status:
            if resource_status[0] != 'Started' or resource_status[1] != init_start_node:
                utils.prt_log(conn,
                              f"Failed to move {resource}, status:{resource_status[0]}", 1)
        else:
            utils.prt_log(conn, f"Can't get status of resource {resource}", 1)
        iscsi_obj.unmove_res(resource)

    def get_log(self, node_down):
        tmp_path = "/tmp/dmesg"
        crm_tmp_path = "/tmp/crm_report"
        lst_get_log = []
        lst_mkdir = []
        lst_download = []
        lst_del_log = []
        log_path = self.config.get_log_path()
        kind = self.config.get_kind()
        utils.prt_log('', f"Start to collect crm and dmesg file ...", 0)

        
        crm_log_path = self.config.get_log_path()
        debug_log = action.DebugLog(self.conn.list_normal_vplx_ssh[0])
        utils.prt_log(self.conn.list_normal_vplx_ssh[0], f"Start to collect crm_report...", 0)
        debug_log.get_crm_report_file(self.crm_start_time, crm_tmp_path)
        debug_log.download_log(crm_tmp_path, crm_log_path)
        debug_log.rm_log_dir(crm_tmp_path)
        if kind != "node_down":
            vplx_ssh = self.conn.list_vplx_ssh
        else:
            vplx_ssh = self.conn.list_normal_vplx_ssh[:]
            if not node_down:
                if self.conn.down_node_ssh:
                    vplx_ssh.append(self.conn.down_node_ssh)
        for conn in vplx_ssh:
            debug_log = action.DebugLog(conn)
            lst_mkdir.append(gevent.spawn(debug_log.mkdir_log_dir, tmp_path))
            lst_get_log.append(gevent.spawn(debug_log.get_dmesg_file, tmp_path))
            lst_download.append(gevent.spawn(debug_log.download_log, tmp_path, log_path))
            lst_del_log.append(gevent.spawn(debug_log.rm_log_dir, tmp_path))
        gevent.joinall(lst_get_log)
        gevent.joinall(lst_mkdir)
        gevent.joinall(lst_download)
        gevent.joinall(lst_mkdir)
        utils.prt_log('', f"Finished to collect crm and dmesg file ...", 0)

    def clean_dmesg(self):
        lst_clean_dmesg = []
        for conn in self.conn.list_vplx_ssh:
            debug_log = action.DebugLog(conn)
            lst_clean_dmesg.append(gevent.spawn(debug_log.clear_dmesg))
        gevent.joinall(lst_clean_dmesg)



    def collect_crm_report_file(self, time, conn):
        tmp_path = "/tmp/crm_report"
        crm_log_path = self.config.get_log_path()
        debug_log = action.DebugLog(conn)
        utils.prt_log(conn, f"Start to collect crm_report...", 0)
        debug_log.get_crm_report_file(time, tmp_path)
        debug_log.download_log(tmp_path, crm_log_path)
        debug_log.rm_log_dir(tmp_path)

class K8sNodes(object):
    def __init__(self, config):
        self.config = config
        self.conn = ConnectNormal(self.config)
    def down_nodes(self):
        for ssh in self.conn.list_nodes_ssh:
            ssh.reboot()


