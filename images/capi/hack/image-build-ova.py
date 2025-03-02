#!/usr/bin/python

# Copyright 2019 The Kubernetes Authors.
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

################################################################################
# usage: image-build-ova.py [FLAGS] ARGS
#  This program builds an OVA file from a VMDK and manifest file generated as a
#  result of a Packer build.
################################################################################

import argparse
import hashlib
import json
import os
import subprocess
from string import Template
import tarfile


def main():
    parser = argparse.ArgumentParser(
        description="Builds an OVA using the artifacts from a Packer build")
    parser.add_argument(dest='build_dir',
                        nargs='?',
                        metavar='BUILD_DIR',
                        default='.',
                        help='The Packer build directory')
    args = parser.parse_args()

    # Change the working directory if one is specified.
    os.chdir(args.build_dir)
    print("image-build-ova: cd %s" % args.build_dir)

    # Load the packer manifest JSON
    data = None
    with open('packer-manifest.json', 'r') as f:
        data = json.load(f)

    # Get the first build.
    build = data['builds'][0]
    build_data = build['custom_data']
    print("image-build-ova: loaded %s-kube-%s" % (build['name'],
                                                  build_data['kubernetes_semver']))

    # Get a list of the VMDK files from the packer manifest.
    vmdk_files = get_vmdk_files(build['files'])

    # Create stream-optimized versions of the VMDK files.
    stream_optimize_vmdk_files(vmdk_files)

    # TODO(akutz) Support multiple VMDK files in the OVF/OVA
    vmdk = vmdk_files[0]

    # Create the OVF file.
    ovf = "%s.ovf" % build['name']
    create_ovf(ovf, {
        'BUILD_DATE': build_data['build_date'],
        'BUILD_NAME': build['name'],
        'ARTIFACT_ID': build['artifact_id'],
        'BUILD_TIMESTAMP': build_data['build_timestamp'],
        'CAPI_VERSION': build_data['capi_version'],
        'CNI_VERSION': build_data['kubernetes_cni_semver'],
        'OS_NAME': build_data['os_name'],
        'ISO_CHECKSUM': build_data['iso_checksum'],
        'ISO_CHECKSUM_TYPE': build_data['iso_checksum_type'],
        'ISO_URL': build_data['iso_url'],
        'KUBERNETES_SEMVER': build_data['kubernetes_semver'],
        'KUBERNETES_SOURCE_TYPE': build_data['kubernetes_source_type'],
        'POPULATED_DISK_SIZE': vmdk['size'],
        'STREAM_DISK_SIZE': vmdk['stream_size'],
    })

    # Create the OVA manifest.
    ova_manifest = "%s.mf" % build['name']
    create_ova_manifest(ova_manifest, [ovf, vmdk['stream_name']])

    # Create the OVA.
    ova = "%s.ova" % build['name']
    create_ova(ova, [ovf, ova_manifest, vmdk['stream_name']])


def sha256(path):
    m = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            data = f.read(65536)
            if not data:
                break
            m.update(data)
    return m.hexdigest()


def create_ova(path, infile_paths):
    print("image-build-ova: create ova %s" % path)
    with open(path, 'wb') as f:
        with tarfile.open(fileobj=f, mode='w|') as tar:
            for infile_path in infile_paths:
                tar.add(infile_path)

    chksum_path = "%s.sha256" % path
    print("image-build-ova: create ova checksum %s" % chksum_path)
    with open(chksum_path, 'w') as f:
        f.write(sha256(path))


def create_ovf(path, data):
    print("image-build-ova: create ovf %s" % path)
    with open(path, 'w') as f:
        f.write(Template(_OVF_TEMPLATE).substitute(data))


def create_ova_manifest(path, infile_paths):
    print("image-build-ova: create ova manifest %s" % path)
    with open(path, 'w') as f:
        for i in infile_paths:
            f.write('SHA256(%s)= %s\n' % (i, sha256(i)))


def get_vmdk_files(inlist):
    outlist = []
    for f in inlist:
        if f['name'].endswith('.vmdk'):
            outlist.append(f)
    return outlist


def stream_optimize_vmdk_files(inlist):
    for f in inlist:
        infile = f['name']
        outfile = infile.replace('.vmdk', '.ova.vmdk', 1)
        if os.path.isfile(outfile):
            os.remove(outfile)
        args = [
            'vmware-vdiskmanager',
            '-r', infile,
            '-t', '5',
            outfile
        ]
        print("image-build-ova: stream optimize %s --> %s (1-2 minutes)" %
              (infile, outfile))
        subprocess.check_call(args)
        f['stream_name'] = outfile
        f['stream_size'] = os.path.getsize(outfile)


_OVF_TEMPLATE = '''<?xml version='1.0' encoding='UTF-8'?>
<Envelope xmlns="http://schemas.dmtf.org/ovf/envelope/1" xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1" xmlns:vmw="http://www.vmware.com/schema/ovf" xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData" xmlns:vssd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_VirtualSystemSettingData">
  <References>
    <File ovf:id="file1" ovf:href="${BUILD_NAME}.ova.vmdk" ovf:size="${STREAM_DISK_SIZE}"/>
  </References>
  <DiskSection>
    <Info>List of the virtual disks</Info>
    <Disk ovf:capacity="20" ovf:capacityAllocationUnits="byte * 2^30" ovf:format="http://www.vmware.com/interfaces/specifications/vmdk.html#streamOptimized" ovf:diskId="vmdisk1" ovf:fileRef="file1" ovf:populatedSize="${POPULATED_DISK_SIZE}"/>
  </DiskSection>
  <NetworkSection>
    <Info>The list of logical networks</Info>
    <Network ovf:name="nic0">
      <Description>Please select a network</Description>
    </Network>
  </NetworkSection>
  <vmw:StorageGroupSection ovf:required="false" vmw:id="group1" vmw:name="vSAN Default Storage Policy">
    <Info>Storage policy for group of disks</Info>
    <vmw:Description>The vSAN Default Storage Policy storage policy group</vmw:Description>
  </vmw:StorageGroupSection>
  <VirtualSystem ovf:id="${ARTIFACT_ID}">
    <Info>A Virtual system</Info>
    <Name>${ARTIFACT_ID}</Name>
    <AnnotationSection>
      <Info>A human-readable annotation</Info>
      <Annotation>Cluster API vSphere image - ${OS_NAME} and Kubernetes ${KUBERNETES_SEMVER} - https://github.com/kubernetes-sigs/cluster-api-provider-vsphere/tree/master/build/images</Annotation>
    </AnnotationSection>
    <OperatingSystemSection ovf:id="101" vmw:osType="other3xLinux64Guest">
      <Info>The operating system installed</Info>
      <Description>Other 3.x or later Linux (64-bit)</Description>
    </OperatingSystemSection>
    <VirtualHardwareSection>
      <Info>Virtual hardware requirements</Info>
      <System>
        <vssd:ElementName>Virtual Hardware Family</vssd:ElementName>
        <vssd:InstanceID>0</vssd:InstanceID>
        <vssd:VirtualSystemType>vmx-11</vssd:VirtualSystemType>
      </System>
      <Item>
        <rasd:AllocationUnits>hertz * 10^6</rasd:AllocationUnits>
        <rasd:Description>Number of Virtual CPUs</rasd:Description>
        <rasd:ElementName>2 virtual CPU(s)</rasd:ElementName>
        <rasd:InstanceID>1</rasd:InstanceID>
        <rasd:ResourceType>3</rasd:ResourceType>
        <rasd:VirtualQuantity>2</rasd:VirtualQuantity>
        <vmw:CoresPerSocket ovf:required="false">2</vmw:CoresPerSocket>
      </Item>
      <Item>
        <rasd:AllocationUnits>byte * 2^20</rasd:AllocationUnits>
        <rasd:Description>Memory Size</rasd:Description>
        <rasd:ElementName>2048MB of memory</rasd:ElementName>
        <rasd:InstanceID>2</rasd:InstanceID>
        <rasd:ResourceType>4</rasd:ResourceType>
        <rasd:VirtualQuantity>2048</rasd:VirtualQuantity>
      </Item>
      <Item>
        <rasd:Address>0</rasd:Address>
        <rasd:Description>SCSI Controller</rasd:Description>
        <rasd:ElementName>SCSI Controller 1</rasd:ElementName>
        <rasd:InstanceID>3</rasd:InstanceID>
        <rasd:ResourceSubType>lsilogic</rasd:ResourceSubType>
        <rasd:ResourceType>6</rasd:ResourceType>
        <vmw:Config ovf:required="false" vmw:key="slotInfo.pciSlotNumber" vmw:value="160"/>
      </Item>
      <Item>
        <rasd:Address>1</rasd:Address>
        <rasd:Description>IDE Controller</rasd:Description>
        <rasd:ElementName>IDE Controller 1</rasd:ElementName>
        <rasd:InstanceID>4</rasd:InstanceID>
        <rasd:ResourceType>5</rasd:ResourceType>
      </Item>
      <Item>
        <rasd:AddressOnParent>0</rasd:AddressOnParent>
        <rasd:ElementName>Hard Disk 1</rasd:ElementName>
        <rasd:HostResource>ovf:/disk/vmdisk1</rasd:HostResource>
        <rasd:InstanceID>5</rasd:InstanceID>
        <rasd:Parent>3</rasd:Parent>
        <rasd:ResourceType>17</rasd:ResourceType>
      </Item>
      <Item>
        <rasd:AddressOnParent>0</rasd:AddressOnParent>
        <rasd:AutomaticAllocation>false</rasd:AutomaticAllocation>
        <rasd:ElementName>CD/DVD Drive 1</rasd:ElementName>
        <rasd:InstanceID>6</rasd:InstanceID>
        <rasd:Parent>4</rasd:Parent>
        <rasd:ResourceSubType>vmware.cdrom.atapi</rasd:ResourceSubType>
        <rasd:ResourceType>15</rasd:ResourceType>
      </Item>
      <Item>
        <rasd:AddressOnParent>0</rasd:AddressOnParent>
        <rasd:AutomaticAllocation>false</rasd:AutomaticAllocation>
        <rasd:Description>Floppy Drive</rasd:Description>
        <rasd:ElementName>Floppy Drive 1</rasd:ElementName>
        <rasd:InstanceID>7</rasd:InstanceID>
        <rasd:ResourceSubType>vmware.floppy.device</rasd:ResourceSubType>
        <rasd:ResourceType>14</rasd:ResourceType>
      </Item>
      <Item>
        <rasd:AddressOnParent>0</rasd:AddressOnParent>
        <rasd:AutomaticAllocation>true</rasd:AutomaticAllocation>
        <rasd:Connection>nic0</rasd:Connection>
        <rasd:ElementName>Network adapter 1</rasd:ElementName>
        <rasd:InstanceID>8</rasd:InstanceID>
        <rasd:ResourceSubType>VmxNet3</rasd:ResourceSubType>
        <rasd:ResourceType>10</rasd:ResourceType>
        <vmw:Config ovf:required="false" vmw:key="slotInfo.pciSlotNumber" vmw:value="192"/>
        <vmw:Config ovf:required="false" vmw:key="connectable.allowGuestControl" vmw:value="true"/>
        <vmw:Config ovf:required="false" vmw:key="wakeOnLanEnabled" vmw:value="false"/>
      </Item>
      <Item ovf:required="false">
        <rasd:ElementName>Video card</rasd:ElementName>
        <rasd:InstanceID>9</rasd:InstanceID>
        <rasd:ResourceType>24</rasd:ResourceType>
        <vmw:Config ovf:required="false" vmw:key="enable3DSupport" vmw:value="false"/>
        <vmw:Config ovf:required="false" vmw:key="graphicsMemorySizeInKB" vmw:value="262144"/>
        <vmw:Config ovf:required="false" vmw:key="useAutoDetect" vmw:value="false"/>
        <vmw:Config ovf:required="false" vmw:key="videoRamSizeInKB" vmw:value="4096"/>
        <vmw:Config ovf:required="false" vmw:key="numDisplays" vmw:value="1"/>
        <vmw:Config ovf:required="false" vmw:key="use3dRenderer" vmw:value="automatic"/>
      </Item>
      <vmw:Config ovf:required="false" vmw:key="flags.vbsEnabled" vmw:value="false"/>
      <vmw:Config ovf:required="false" vmw:key="cpuHotAddEnabled" vmw:value="false"/>
      <vmw:Config ovf:required="false" vmw:key="nestedHVEnabled" vmw:value="false"/>
      <vmw:Config ovf:required="false" vmw:key="virtualSMCPresent" vmw:value="false"/>
      <vmw:Config ovf:required="false" vmw:key="flags.vvtdEnabled" vmw:value="false"/>
      <vmw:Config ovf:required="false" vmw:key="cpuHotRemoveEnabled" vmw:value="false"/>
      <vmw:Config ovf:required="false" vmw:key="memoryHotAddEnabled" vmw:value="false"/>
      <vmw:Config ovf:required="false" vmw:key="bootOptions.efiSecureBootEnabled" vmw:value="false"/>
      <vmw:Config ovf:required="false" vmw:key="firmware" vmw:value="bios"/>
      <vmw:Config ovf:required="false" vmw:key="virtualICH7MPresent" vmw:value="false"/>
    </VirtualHardwareSection>
    <vmw:StorageSection ovf:required="false" vmw:group="group1">
      <Info>Storage policy group reference</Info>
    </vmw:StorageSection>
    <EulaSection>
      <Info>An end-user license agreement</Info>
      <License>VMWARE END USER LICENSE AGREEMENT



PLEASE NOTE THAT THE TERMS OF THIS END USER LICENSE AGREEMENT SHALL GOVERN YOUR USE OF THE SOFTWARE, REGARDLESS OF ANY TERMS THAT MAY APPEAR DURING THE INSTALLATION OF THE SOFTWARE. 



IMPORTANT-READ CAREFULLY:   BY DOWNLOADING, INSTALLING, OR USING THE SOFTWARE, YOU (THE INDIVIDUAL OR LEGAL ENTITY) AGREE TO BE BOUND BY THE TERMS OF THIS END USER LICENSE AGREEMENT ("EULA").  IF YOU DO NOT AGREE TO THE TERMS OF THIS EULA, YOU MUST NOT DOWNLOAD, INSTALL, OR USE THE SOFTWARE, AND YOU MUST DELETE OR RETURN THE UNUSED SOFTWARE TO THE VENDOR FROM WHICH YOU ACQUIRED IT WITHIN THIRTY (30) DAYS AND REQUEST A REFUND OF THE LICENSE FEE, IF ANY, THAT YOU PAID FOR THE SOFTWARE.



EVALUATION LICENSE.  If You are licensing the Software for evaluation purposes, Your use of the Software is only permitted in a non-production environment and for the period limited by the License Key.  Notwithstanding any other provision in this EULA, an Evaluation License of the Software is provided "AS-IS" without indemnification, support or warranty of any kind, expressed or implied.



1.	DEFINITIONS.

 

1.1	 "Affiliate" means, with respect to a party at a given time, an entity that then is directly or indirectly controlled by, is under common control with, or controls that party, and here "control" means an ownership, voting or similar interest representing fifty percent (50%) or more of the total interests then outstanding of that entity.



1.2	"Documentation" means that documentation that is generally provided to You by VMware with the Software, as revised by VMware from time to time, and which may include end user manuals, operation instructions, installation guides, release notes, and on-line help files regarding the use of the Software.



1.3	"Guest Operating Systems" means instances of third-party operating systems licensed by You, installed in a Virtual Machine and run using the Software.



1.4	"Intellectual Property Rights" means all worldwide intellectual property rights, including without limitation, copyrights, trademarks, service marks, trade secrets, know how, inventions, patents, patent applications, moral rights and all other proprietary rights, whether registered or unregistered. 



1.5	"License" means a license granted under Section 2.1 (General License Grant). 



1.6    	"License Key" means a serial number that enables You to activate and use the Software.



1.7	"License Term" means the duration of a License as specified in the Order.



1.8	"License Type" means the type of License applicable to the Software, as more fully described in the Order.



1.9 "Open Source Software" or "OSS" means software components embedded in the Software and provided under separate license terms, which can be found either in the open_source_licenses.txt file (or similar file) provided within the Software or at www.vmware.com/download/open_source.html. 



1.10 "Order" means a purchase order, enterprise license agreement, or other ordering document issued by You to VMware or a VMware authorized reseller that references and incorporates this EULA and is accepted by VMware as set forth in Section 4 (Order). 

1.11 "Product Guide" means the current version of the VMware Product Guide at the time of Your Order, copies of which are found at www.vmware.com/download/eula.

  

1.12 "Support Services Terms" means VMware's then-current support policies, copies of which are posted at www.vmware.com/support/policies.



1.13	"Software" means the VMware Tools and the VMware computer programs listed on VMware's commercial price list to which You acquire a license under an Order, together with any software code relating to the foregoing that is provided to You pursuant to a support and subscription service contract and that is not subject to a separate license agreement.



1.14 "Territory" means the country or countries in which You have been invoiced; provided, however, that if You have been invoiced within any of the European Economic Area member states, You may deploy the corresponding Software throughout the European Economic Area. 



1.15 "Third Party Agent" means a third party delivering information technology services to You pursuant to a written contract with You.



1.16	"Virtual Machine" means a software container that can run its own operating system and execute applications like a physical machine.   



1.17	"VMware" means VMware, Inc., a Delaware corporation, if You are purchasing Licenses or services for use in the United States and VMware International Limited, a company organized and existing under the laws of Ireland, for all other purchases.

1.18	"VMware Tools" means the suite of utilities and drivers, Licensed by VMware under the "VMware Tools" name, that can be installed in a Guest Operating System to enhance the performance and functionality of a Guest Operating System when running in a Virtual Machine.



2.		LICENSE GRANT.



2.1	General License Grant.  VMware grants to You a non-exclusive, non-transferable (except as set forth in Section 12.1 (Transfers; Assignment)) license to use the Software and the Documentation during the period of the license and within the Territory, solely for Your internal business operations, and subject to the provisions of the Product Guide. Unless otherwise indicated in the Order, licenses granted to You will be perpetual, will be for use of object code only, and will commence on either delivery of the physical media or the date You are notified of availability for electronic download.  



2.2	Third Party Agents.  Under the License granted to You in Section 2.1 (General License Grant) above, You may permit Your Third Party Agents to access, use and/or operate the Software on Your behalf for the sole purpose of delivering services to You, provided that You will be fully responsible for Your Third Party Agents' compliance with terms and conditions of this EULA and any breach of this EULA by a Third Party Agent shall be deemed to be a breach by You. 



2.3       Copying Permitted.  You may copy the Software and Documentation as necessary to install and run the quantity of copies licensed, but otherwise for archival purposes only. 



2.4	Benchmarking.  You may use the Software to conduct internal performance testing and benchmarking studies. You may only publish or otherwise distribute the results of such studies to third parties as follows:  (a) if with respect to VMware's Workstation or Fusion products, only if You provide a copy of Your study to benchmark@vmware.com prior to distribution;   (b) if with respect to any other Software, only if VMware has reviewed and approved of the methodology, assumptions and other parameters of the study  (please contact VMware at benchmark@vmware.com to request such review and approval) prior to such publication and distribution. 



2.5	VMware Tools.  You may distribute the VMware Tools to third parties solely when installed in a Guest Operating System within a Virtual Machine. You are liable for compliance by those third parties with the terms and conditions of this EULA. 



2.6	Open Source Software.  Notwithstanding anything herein to the contrary, Open Source Software is licensed to You under such OSS's own applicable license terms, which can be found in the open_source_licenses.txt file, the Documentation or as applicable, the corresponding source files for the Software available at www.vmware.com/download/open_source.html. These OSS license terms are consistent with the license granted in Section 2 (License Grant), and may contain additional rights benefiting You.  The OSS license terms shall take precedence over this EULA to the extent that this EULA imposes greater restrictions on You than the applicable OSS license terms. To the extent the license for any Open Source Software requires VMware to make available to You the corresponding source code and/or modifications (the "Source Files"), You may obtain a copy of the applicable Source Files from VMware's website at www.vmware.com/download/open_source.html or by sending a written request, with Your name and address to: VMware, Inc., 3401 Hillview Avenue, Palo Alto, CA 94304, United States of America. All requests should clearly specify:  Open Source Files Request, Attention: General Counsel.  This offer to obtain a copy of the Source Files is valid for three years from the date You acquired this Software.



3.	RESTRICTIONS; OWNERSHIP.



3.1	License Restrictions.  Without VMware's prior written consent, You must not, and must not allow any third party to: (a) use Software in an application services provider, service bureau, or similar capacity for third parties, except that You may use the Software to deliver hosted services to Your Affiliates; (b) disclose to any third party the results of any benchmarking testing or comparative or competitive analyses of VMware's Software done by or on behalf of You, except as specified in Section 2.4 (Benchmarking); (c) make available Software in any form to anyone other than Your employees or contractors reasonably acceptable to VMware and require access to use Software on behalf of You in a matter permitted by this EULA, except as specified in Section 2.2 (Third Party Agents); (d) transfer or sublicense Software or Documentation to an Affiliate or any third party, except as expressly permitted in Section 12.1 (Transfers; Assignment); (e) use Software in conflict with the terms and restrictions of the Software's licensing model and other requirements specified in Product Guide and/or VMware quote; (f) except to the extent permitted by applicable mandatory law, modify, translate, enhance, or create derivative works from the Software, or  reverse engineer, decompile, or otherwise attempt to derive source code from the Software, except as specified in Section 3.2 (Decompilation); (g) remove any copyright or other proprietary notices on or in any copies of Software; or (h) violate or circumvent any technological restrictions within the Software or specified in this EULA, such as via software or services.  



3.2	Decompilation.  Notwithstanding the foregoing, decompiling the Software is permitted to the extent the laws of the Territory give You the express right to do so to obtain information necessary to render the Software interoperable with other software; provided, however, You must first request such information from VMware, provide all reasonably requested information to allow VMware to assess Your claim, and VMware may, in its discretion, either provide such interoperability information to You, impose reasonable conditions, including a reasonable fee, on such use of the Software, or offer to provide alternatives to ensure that VMware's proprietary rights in the Software are protected and to reduce any adverse impact on VMware's proprietary rights.



3.3	Ownership.  The Software and Documentation, all copies and portions thereof, and all improvements, enhancements, modifications and derivative works thereof, and all Intellectual Property Rights therein, are and shall remain the sole and exclusive property of VMware and its licensors. Your rights to use the Software and Documentation shall be limited to those expressly granted in this EULA and any applicable Order.  No other rights with respect to the Software or any related Intellectual Property Rights are implied.  You are not authorized to use (and shall not permit any third party to use) the Software, Documentation or any portion thereof except as expressly authorized by this EULA or the applicable Order.  VMware reserves all rights not expressly granted to You. VMware does not transfer any ownership rights in any Software.



3.4	Guest Operating Systems.  Certain Software allows Guest Operating Systems and application programs to run on a computer system. You acknowledge that You are responsible for obtaining and complying with any licenses necessary to operate any such third-party software.



4.	ORDER.  Your Order is subject to this EULA.  No Orders are binding on VMware until accepted by VMware.  Orders for Software are deemed to be accepted upon VMware's delivery of the Software included in such Order. Orders issued to VMware do not have to be signed to be valid and enforceable.



5.	RECORDS AND AUDIT.  During the License Term for Software and for two (2) years after its expiration or termination, You will maintain accurate records of Your use of the Software sufficient to show compliance with the terms of this EULA. During this period, VMware will have the right to audit Your use of the Software to confirm compliance with the terms of this EULA. That audit is subject to reasonable notice by VMware and will not unreasonably interfere with Your business activities. VMware may conduct no more than one (1) audit in any twelve (12) month period, and only during normal business hours. You will reasonably cooperate with VMware and any third party auditor and will, without prejudice to other rights of VMware, address any non-compliance identified by the audit by promptly paying additional fees. You will promptly reimburse VMware for all reasonable costs of the audit if the audit reveals either underpayment of more than five (5%) percent of the Software fees payable by You for the period audited, or that You have materially failed to maintain accurate records of Software use. 



6.	SUPPORT AND SUBSCRIPTION SERVICES.  Except as expressly specified in the Product Guide, VMware does not provide any support or subscription services for the Software under this EULA.  You have no rights to any updates, upgrades or extensions or enhancements to the Software developed by VMware unless you separately purchase VMware support or subscription services.  These support or subscription services are subject to the Support Services Terms.



7.    WARRANTIES.



7.1 Software Warranty, Duration and Remedy.  VMware warrants to You that the Software will, for a period of ninety (90) days following notice of availability for electronic download or delivery ("Warranty Period"), substantially conform to the applicable Documentation, provided that the Software: (a) has been properly installed and used at all times in accordance with the applicable Documentation; and (b) has not been modified or added to by persons other than VMware or its authorized representative. VMware will, at its own expense and as its sole obligation and Your exclusive remedy for any breach of this warranty, either replace that Software or correct any reproducible error in that Software reported to VMware by You in writing during the Warranty Period. If VMware determines that it is unable to correct the error or replace the Software, VMware will refund to You the amount paid by You for that Software, in which case the License for that Software will terminate.



7.2 Software Disclaimer of Warranty.  OTHER THAN THE WARRANTY ABOVE, AND TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, VMWARE AND ITS SUPPLIERS MAKE NO OTHER EXPRESS WARRANTIES UNDER THIS EULA, AND DISCLAIM ALL IMPLIED WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE, TITLE AND NON-INFRINGEMENT, AND ANY WARRANTY ARISING BY STATUTE, OPERATION OF LAW, COURSE OF DEALING OR PERFORMANCE, OR USAGE OF TRADE. VMWARE AND ITS LICENSORS DO NOT WARRANT THAT THE SOFTWARE WILL OPERATE UNINTERRUPTED OR THAT IT WILL BE FREE FROM DEFECTS OR THAT IT WILL MEET YOUR REQUIREMENTS. 



8.     INTELLECTUAL PROPERTY INDEMNIFICATION. 



8.1 Defense and Indemnification.  Subject to the remainder of this Section 8 (Intellectual Property Indemnification), VMware shall defend You against any third party claim that the Software infringes any patent, trademark or copyright of such third party, or misappropriates a trade secret (but only to the extent that the misappropriation is not a result of Your actions) under the laws of: (a) the United States and Canada; (b) the European Economic Area; (c) Australia; (d) New Zealand; (e) Japan; or (f) the People's Republic of China, to the extent that such countries are part of the Territory for the License ("Infringement Claim") and indemnify You from the resulting costs and damages finally awarded against You to such third party by a court of competent jurisdiction or agreed to in settlement. The foregoing obligations are applicable only if You:  (i) promptly notify VMware in writing of the Infringement Claim; (ii) allow VMware sole control over the defense for the claim and any settlement negotiations; and (iii) reasonably cooperate in response to VMware requests for assistance.  You may not settle or compromise any Infringement Claim without the prior written consent of VMware.

8.2 Remedies.  If the alleged infringing Software become, or in VMware's opinion be likely to become, the subject of an Infringement Claim, VMware will, at VMware's option and expense, do one of the following:  (a) procure the rights necessary for You to make continued use of the affected Software; (b) replace or modify the affected Software to make it non-infringing; or (c) terminate the License to the affected Software and discontinue the related support services, and, upon Your certified deletion of the affected Software, refund: (i) the fees paid by You for the License to the affected Software, less straight-line depreciation over a three (3) year useful life beginning on the date such Software was delivered; and (ii) any pre-paid service fee attributable to related support services to be delivered after the date such service is stopped. Nothing in this Section 8.2 (Remedies) shall limit VMware's obligation under Section 8.1 (Defense and Indemnification) to defend and indemnify You, provided that You replace the allegedly infringing Software upon VMware's making alternate Software available to You and/or You discontinue using the allegedly infringing Software upon receiving VMware's notice terminating the affected License.

8.3 Exclusions.  Notwithstanding the foregoing, VMware will have no obligation under this Section 8 (Intellectual Property Indemnification) or otherwise with respect to any claim based on:  (a) a combination of Software with non-VMware products (other than non-VMware products that are listed on the Order and used in an unmodified form); (b) use for a purpose or in a manner for which the Software was not designed; (c) use of any older version of the Software when use of a newer VMware version would have avoided the infringement; (d) any modification to the Software made without VMware's express written approval; (e) any claim that relates to open source software or freeware technology or any derivatives or other adaptations thereof that is not embedded by VMware into Software listed on VMware's commercial price list; or (f) any Software provided on a no charge, beta or evaluation basis.  THIS SECTION 8 (INTELLECTUAL PROPERTY INDEMNIFICATION) STATES YOUR SOLE AND EXCLUSIVE REMEDY AND VMWARE'S ENTIRE LIABILITY FOR ANY INFRINGEMENT CLAIMS OR ACTIONS. 



9. LIMITATION OF LIABILITY. 



9.1 Limitation of Liability.  TO THE MAXIMUM EXTENT MANDATED BY LAW, IN NO EVENT WILL VMWARE AND ITS LICENSORS BE LIABLE FOR ANY LOST PROFITS OR BUSINESS OPPORTUNITIES, LOSS OF USE, LOSS OF REVENUE, LOSS OF GOODWILL, BUSINESS INTERRUPTION, LOSS OF DATA, OR ANY INDIRECT, SPECIAL, INCIDENTAL, OR CONSEQUENTIAL DAMAGES UNDER ANY THEORY OF LIABILITY, WHETHER BASED IN CONTRACT, TORT, NEGLIGENCE, PRODUCT LIABILITY, OR OTHERWISE.  BECAUSE SOME JURISDICTIONS DO NOT ALLOW THE EXCLUSION OR LIMITATION OF LIABILITY FOR CONSEQUENTIAL OR INCIDENTAL DAMAGES, THE PRECEDING LIMITATION MAY NOT APPLY TO YOU.  VMWARE'S AND ITS LICENSORS' LIABILITY UNDER THIS EULA WILL NOT, IN ANY EVENT, REGARDLESS OF WHETHER THE CLAIM IS BASED IN CONTRACT, TORT, STRICT LIABILITY, OR OTHERWISE, EXCEED THE GREATER OF THE LICENSE FEES YOU PAID FOR THE SOFTWARE GIVING RISE TO THE CLAIM OR $$5000. THE FOREGOING LIMITATIONS SHALL APPLY REGARDLESS OF WHETHER VMWARE OR ITS LICENSORS HAVE BEEN ADVISED OF THE POSSIBILITY OF SUCH DAMAGES AND REGARDLESS OF WHETHER ANY REMEDY FAILS OF ITS ESSENTIAL PURPOSE. 



9.2 Further Limitations.  VMware's licensors shall have no liability of any kind under this EULA and VMware's liability with respect to any third party software embedded in the Software shall be subject to Section 9.1 (Limitation of Liability).  You may not bring a claim under this EULA more than eighteen (18) months after the cause of action arises.



10.     TERMINATION.  

10.1	EULA Term. The term of this EULA begins on the notice of availability for electronic download or delivery of the Software and continues until this EULA is terminated in accordance with this Section 10.

10.2	Termination for Breach.  VMware may terminate this EULA effective immediately upon written notice to You if: (a) You fail to pay any portion of the fees under an applicable Order within ten (10) days after receiving written notice from VMware that payment is past due; or (b) You breach any other provision of this EULA and fail to cure within thirty (30) days after receipt of VMware's written notice thereof. 

10.3	Termination for Insolvency.  VMware may terminate this EULA effective immediately upon written notice to You if You: (a) terminate or suspend your business; (b) become insolvent, admit in writing Your inability to pay Your debts as they mature, make an assignment for the benefit of creditors; or become subject to control of a trustee, receiver or similar authority; or (c) become subject to any bankruptcy or insolvency proceeding.

10.4	Effect of Termination.  Upon VMware's termination of this EULA: (a) all Licensed rights to all Software granted to You under this EULA will immediately cease; and (b) You must cease all use of all Software, and return or certify destruction of all Software and License Keys (including copies) to VMware, and return, or if requested by VMware, destroy, any related VMware Confidential Information in Your possession or control and certify in writing to VMware that You have fully complied with these requirements. Any provision will survive any termination or expiration if by its nature and context it is intended to survive, including Sections 1 (Definitions), 2.6 (Open Source Software), 3 (Restrictions; Ownership), 5 (Records and Audit), 7.2 (Software Disclaimer of Warranty), 9 (Limitation of Liability), 10 (Termination), 11 (Confidential Information) and 12 (General).



11.	CONFIDENTIAL INFORMATION.  



11.1	Definition.  "Confidential Information"  means information or materials provided by one party ("Discloser") to the other party ("Recipient") which are in tangible form and labelled "confidential" or the like, or, information which a reasonable person knew or should have known to be confidential.  The following information shall be considered Confidential Information whether or not marked or identified as such:  (a) License Keys; (b) information regarding VMware's pricing, product roadmaps or strategic marketing plans; and (c) non-public materials relating to the Software.



11.2	Protection.  Recipient may use Confidential Information of Discloser; (a) to exercise its rights and perform its obligations under this EULA; or (b) in connection with the parties' ongoing business relationship.  Recipient will not use any Confidential Information of Discloser for any purpose not expressly permitted by this EULA, and will disclose the Confidential Information of Discloser only to the employees or contractors of Recipient who have a need to know such Confidential Information for purposes of this EULA and who are under a duty of confidentiality no less restrictive than Recipient's duty hereunder.  Recipient will protect Confidential Information from unauthorized use, access, or disclosure in the same manner as Recipient protects its own confidential or proprietary information of a similar nature but with no less than reasonable care.

11.3 Exceptions.  Recipient's obligations under Section 11.2 (Protection) with respect to any Confidential Information will terminate if Recipient can show by written records that such information:  (a) was already known to Recipient at the time of disclosure by Discloser; (b) was disclosed to Recipient by a third party who had the right to make such disclosure without any confidentiality restrictions; (c) is, or through no fault of Recipient has become, generally available to the public; or (d) was independently developed by Recipient without access to, or use of, Discloser's Information.  In addition, Recipient will be allowed to disclose Confidential Information to the extent that such disclosure is required by law or by the order of a court of similar judicial or administrative body, provided that Recipient notifies Discloser of such required disclosure promptly and in writing and cooperates with Discloser, at Discloser's request and expense, in any lawful action to contest or limit the scope of such required disclosure.

11.4	Data Privacy.  You agree that VMware may process technical and related information about Your use of the Software which may include internet protocol address, hardware identification, operating system, application software, peripheral hardware, and non-personally identifiable Software usage statistics to facilitate the provisioning of updates, support, invoicing or online services and may transfer such information to other companies in the VMware worldwide group of companies from time to time. To the extent that this information constitutes personal data, VMware shall be the controller of such personal data. To the extent that it acts as a controller, each party shall comply at all times with its obligations under applicable data protection legislation. 



12.	GENERAL.



12.1	Transfers; Assignment.  Except to the extent transfer may not legally be restricted or as permitted by VMware's transfer and assignment policies, in all cases following the process set forth at www.vmware.com/support/policies/licensingpolicies.html, You will not assign this EULA, any Order, or any right or obligation herein or delegate any performance without VMware's prior written consent, which consent will not be unreasonably withheld. Any other attempted assignment or transfer by You will be void. VMware may use its Affiliates or other sufficiently qualified subcontractors to provide services to You, provided that VMware remains responsible to You for the performance of the services.



12.2	Notices.  Any notice delivered by VMware to You under this EULA will be delivered via mail, email or fax. 



12.3	Waiver.  Failure to enforce a provision of this EULA will not constitute a waiver.

12.4     Severability.  If any part of this EULA is held unenforceable, the validity of all remaining parts will not be affected.

12.5	Compliance with Laws; Export Control; Government Regulations. Each party shall comply with all laws applicable to the actions contemplated by this EULA. You acknowledge that the Software is of United States origin, is provided subject to the U.S. Export Administration Regulations, may be subject to the export control laws of the applicable territory, and that diversion contrary to applicable export control laws is prohibited. You represent that (1) you are not, and are not acting on behalf of, (a) any person who is a citizen, national, or resident of, or who is controlled by the government of any country to which the United States has prohibited export transactions; or (b) any person or entity listed on the U.S. Treasury Department list of Specially Designated Nationals and Blocked Persons, or the U.S. Commerce Department Denied Persons List or Entity List; and (2) you will not permit the Software to be used for, any purposes prohibited by law, including, any prohibited development, design, manufacture or production of missiles or nuclear, chemical or biological weapons. The Software and accompanying documentation are deemed to be "commercial computer software" and "commercial computer software documentation", respectively, pursuant to DFARS Section 227.7202 and FAR Section 12.212(b), as applicable.  Any use, modification, reproduction, release, performing, displaying or disclosing of the Software and documentation by or for the U.S. Government shall be governed solely by the terms and conditions of this EULA.

12.6	Construction.  The headings of sections of this EULA are for convenience and are not to be used in interpreting this EULA. As used in this EULA, the word 'including' means "including but not limited to".

12.7	Governing Law.  This EULA is governed by the laws of the State of California, United States of America (excluding its conflict of law rules), and the federal laws of the United States. To the extent permitted by law, the state and federal courts located in Santa Clara County, California will be the exclusive jurisdiction for disputes arising out of or in connection with this EULA. The U.N. Convention on Contracts for the International Sale of Goods does not apply. 

12.8	Third Party Rights.  Other than as expressly set out in this EULA, this EULA does not create any rights for any person who is not a party to it, and no person who is not a party to this EULA may enforce any of its terms or rely on any exclusion or limitation contained in it. 

12.9	Order of Precedence.  In the event of conflict or inconsistency among the Product Guide, this EULA and the Order, the following order of precedence shall apply: (a) the Product Guide, (b) this EULA and (c) the Order. With respect to any inconsistency between this EULA and an Order, the terms of this EULA shall supersede and control over any conflicting or additional terms and conditions of any Order, acknowledgement or confirmation or other document issued by You. 

12.10  Entire Agreement.  This EULA, including accepted Orders and any amendments hereto, and the Product Guide contain the entire agreement of the parties with respect to the subject matter of this EULA and supersede all previous or contemporaneous communications, representations, proposals, commitments, understandings and agreements, whether written or oral, between the parties regarding the subject matter hereof.  This EULA may be amended only in writing signed by authorized representatives of both parties.

12.11  Contact Information.  Please direct legal notices or other correspondence to VMware, Inc., 3401 Hillview Avenue, Palo Alto, California 94304, United States of America, Attention: Legal Department.</License>
    </EulaSection>
    <ProductSection>
      <Info>Information about the installed software</Info>
      <Product>${OS_NAME} and Kubernetes ${KUBERNETES_SEMVER}</Product>
      <Vendor>VMware Inc.</Vendor>
      <Version>kube-${KUBERNETES_SEMVER}</Version>
      <FullVersion>kube-${KUBERNETES_SEMVER}</FullVersion>
      <ProductUrl>https://github.com/kubernetes-sigs/cluster-api-provider-vsphere</ProductUrl>
      <VendorUrl>https://vmware.com</VendorUrl>
      <Category>Cluster API Provider (CAPI)</Category>
      <Property ovf:userConfigurable="false" ovf:value="${BUILD_TIMESTAMP}" ovf:type="string" ovf:key="BUILD_TIMESTAMP"></Property>
      <Property ovf:userConfigurable="false" ovf:value="${BUILD_DATE}" ovf:type="string" ovf:key="BUILD_DATE"></Property>
      <Property ovf:userConfigurable="false" ovf:value="${CAPI_VERSION}" ovf:type="string" ovf:key="CAPI_VERSION"></Property>
      <Property ovf:userConfigurable="false" ovf:value="${CNI_VERSION}" ovf:type="string" ovf:key="CNI_VERSION"></Property>
      <Property ovf:userConfigurable="false" ovf:value="${ISO_URL}" ovf:type="string" ovf:key="ISO_URL"></Property>
      <Property ovf:userConfigurable="false" ovf:value="${ISO_CHECKSUM}" ovf:type="string" ovf:key="ISO_CHECKSUM"></Property>
      <Property ovf:userConfigurable="false" ovf:value="${ISO_CHECKSUM_TYPE}" ovf:type="string" ovf:key="ISO_CHECKSUM_TYPE"></Property>
      <Property ovf:userConfigurable="false" ovf:value="${KUBERNETES_SEMVER}" ovf:type="string" ovf:key="KUBERNETES_SEMVER"></Property>
      <Property ovf:userConfigurable="false" ovf:value="${KUBERNETES_SOURCE_TYPE}" ovf:type="string" ovf:key="KUBERNETES_SOURCE_TYPE"></Property>
    </ProductSection>
  </VirtualSystem>
</Envelope>
'''

if __name__ == "__main__":
    main()
