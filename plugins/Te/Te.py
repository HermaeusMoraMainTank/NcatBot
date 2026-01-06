import time
import random

import requests

from common.constants.HMMT import HMMT
from main import on_group_message
from ncatbot.utils import get_log
from ncatbot.core import GroupMessage
from ncatbot.plugin_system import NcatBotPlugin, on_message
from Crypto.Cipher import AES
import base64
import hashlib
import json
_log = get_log()




class Te(NcatBotPlugin):
    name = "随机视频"  # 插件名称
    version = "1.0"  # 插件版本
    pix="-p9B[~PnPs"
    key = "Vq234zBeSdGgYXzVTEfnnjjdmaTkk7A4"
    baseurl="https://spi.d5c4a712.com"
    dataurl=[]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.GetVidoList()

    def pkcs7_unpad(self,data: bytes, block_size: int) -> bytes:
        """
        移除 PKCS7 填充
        """
        if len(data) == 0 or len(data) % block_size != 0:
            raise ValueError("数据长度不是块大小的整数倍")
        pad_len = data[-1]
        if pad_len < 1 or pad_len > block_size:
            raise ValueError("填充长度非法")
        if data[-pad_len:] != bytes([pad_len]) * pad_len:
            raise ValueError("填充内容非法")
        return data[:-pad_len]

    def aesDecrypt(self,encryptedData: bytes, key: bytes, pix: bytes, suffix: bytes) -> str:
        # ------------------------------
        # 步骤 1：生成完整 IV（IV = pix + suffix）
        # ------------------------------
        iv = pix + suffix

        # 检查 IV 长度是否符合 AES 要求（必须 16 字节）
        if len(iv) != AES.block_size:
            raise ValueError("IV 长度必须为 16 字节（AES 块大小）")

        # ------------------------------
        # 步骤 2：检查密钥长度（16/24/32 字节）
        # ------------------------------
        keyLen = len(key)
        if keyLen not in (16, 24, 32):
            raise ValueError("密钥长度必须为 16/24/32 字节（对应 AES-128/192/256）")

        # ------------------------------
        # 步骤 3 & 4：创建 AES 块并初始化 CBC 解密器
        # ------------------------------
        cipher = AES.new(key, AES.MODE_CBC, iv)

        # ------------------------------
        # 步骤 5：执行解密操作
        # ------------------------------
        decryptedData = cipher.decrypt(encryptedData)

        # ------------------------------
        # 步骤 6：移除 PKCS7 填充
        # ------------------------------
        unpaddedData = self.pkcs7_unpad(decryptedData, AES.block_size)

        # ------------------------------
        # 步骤 7：转换为 UTF-8 字符串
        # ------------------------------
        try:
            return unpaddedData.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("解密后数据非合法 UTF-8 编码")

    def pkcs7_pad(self,data: bytes, block_size: int) -> bytes:
        """
        PKCS7 填充
        """
        pad_len = block_size - (len(data) % block_size)
        return data + bytes([pad_len]) * pad_len

    def aesEncrypt(self,plaintext: str, dynamicParam: str, ivPrefix: str, key: str) -> str:
        # ------------------------------
        # 步骤 1：转换参数为字节数组（UTF-8 编码）
        # ------------------------------
        keyBytes = key.encode("utf-8")
        ivPrefixBytes = ivPrefix.encode("utf-8")
        dynamicParamBytes = dynamicParam.encode("utf-8")
        ivBytes = ivPrefixBytes + dynamicParamBytes

        plaintextBytes = plaintext.encode("utf-8")

        # 检查 IV 长度是否符合 AES 要求（必须 16 字节）
        if len(ivBytes) != AES.block_size:
            raise ValueError("IV 长度必须为 16 字节（AES 块大小）")

        # 检查密钥长度（16/24/32 字节）
        if len(keyBytes) not in (16, 24, 32):
            raise ValueError("密钥长度必须为 16/24/32 字节（对应 AES-128/192/256）")

        # ------------------------------
        # 步骤 2：创建 AES 块（底层加密算法核心）
        # ------------------------------
        cipher = AES.new(keyBytes, AES.MODE_CBC, ivBytes)

        # ------------------------------
        # 步骤 3：对明文进行 PKCS7 填充
        # ------------------------------
        paddedPlaintext = self.pkcs7_pad(plaintextBytes, AES.block_size)

        # ------------------------------
        # 步骤 4：初始化 CBC 加密器并执行加密
        # ------------------------------
        cipherText = cipher.encrypt(paddedPlaintext)

        # ------------------------------
        # 步骤 5：Base64 编码输出
        # ------------------------------
        return base64.b64encode(cipherText).decode("utf-8")
    def GetVidoList(self,):
        # 1k就完事了 行了 先试试效果我懒得弄了
        d = {"page": 1, "list_row": 1000, "type": 2, "timestamp": int(time.time() * 1000)}
        s = "&".join([f"{k}={v}" for k, v in d.items()])
        split = s.split("&")
        split.sort()
        ns = "&".join(split) + "&"
        secret = "m}q%ea6:LDcmS?aK)CeF287bPvd99@E,9Up^"
        md5 = hashlib.md5()
        md5.update((ns + secret).encode("utf-8"))
        encodeSign = md5.hexdigest()
        d["encode_sign"] = encodeSign
        bytes_json = json.dumps(d, ensure_ascii=False)
        encrypt =self.aesEncrypt(bytes_json, "suffix", self.pix, self.key)
        postdata = {"post-data": encrypt}
        jsons = json.dumps(postdata, ensure_ascii=False)
        headers = {"suffix": "suffix","Content-Type": "application/json"}
        response = requests.post(self.baseurl + "/video/listcache",headers=headers, data=jsons).json()
        try:
            a = self.aesDecrypt(base64.b64decode(response.get("data")), self.key.encode(), self.pix.encode(),response.get("suffix", {}).encode())
            jsons = json.loads(a)
            value = jsons.get("data", {}).get("data", None)
            if len(value) != 0:
                self.dataurl=value
        except Exception as e:
            _log.error("唉 好像出错了 我不知道错在哪里不管了")
            _log.error(e)

    @on_message
    async def Te_handler(self,msg: GroupMessage):
        if len(self.dataurl) == 0:
            return
        if msg.raw_message not in ["随机视频"]:
            return
        #唉 这个东西 我就限制一下
        if msg.sender.user_id not in [HMMT.HMMT_ID, "1271701079"]:
            return
        #必须在小群里
        if msg.group_id not in["1064163905","975869984"]:
            return

        value = random.choice(self.dataurl)
        await self.api.post_group_msg(
            group_id=msg.group_id,
            text=f"{value.get('link')}",
        )





