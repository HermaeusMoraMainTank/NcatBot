import base64
import json
import requests
from Cryptodome.Cipher import AES, PKCS1_OAEP
from Cryptodome.PublicKey import RSA
from Cryptodome.Util.Padding import pad

Aeskey = "0CoJUm6Qyw8W8jud"
Aesiv = "0102030405060708"
modulushex = "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7"
exponenthex    = "010001"
csrftoken    = "7d327f98beb7cb91ebc9ad1fd50f4d19"
def Aesencrypt(plain_text, key_str, iv):
    key = key_str.encode('utf-8')
    iv_bytes = iv.encode('utf-8')
    plain_data = plain_text.encode('utf-8')
    cipher = AES.new(key, AES.MODE_CBC, iv_bytes)
    ciphertext = cipher.encrypt(pad(plain_data, AES.block_size))

    # Base64编码
    return base64.b64encode(ciphertext).decode('utf-8')

def Rsaencrypt(sec_key, pub_key, modulus):
    reversed_key = sec_key[::-1]
    hex_key = ''.join(format(ord(c), 'x') for c in reversed_key)
    key_int = int(hex_key, 16)
    pub_int = int(pub_key, 16)
    mod_int = int(modulus, 16)

    # RSA加密：c = m^e mod n
    result = pow(key_int, pub_int, mod_int)

    # 转换为16进制并填充
    hex_result = format(result, 'x')

    # 计算需要的填充长度
    modulus_len = len(modulus.lstrip('0'))
    padding_len = max(0, modulus_len - len(hex_result))

    return '0' * padding_len + hex_result

def buildreqdata(params, enc_sec_key):
    # 创建一个字典来存储参数
    data = {
        'params': params,
        'encSecKey': enc_sec_key
    }
    # URL 编码参数
    # 返回编码后的数据
    return data


def weapi_encrypt(s: str):
    one = Aesencrypt(s, Aeskey, Aesiv)
    params = Aesencrypt(one, Aeskey, Aesiv)
    encSecKey = Rsaencrypt(Aeskey,exponenthex,modulushex)
    return params, encSecKey
def setheader():
    headers = {
        'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36 Edg/136.0.0.0",
        'accept-language': "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,ja;q=0.5",
        'nm-gcore-status': "1",
        'origin': "https://music.163.com",
        'priority': "u=1, i",
        'referer': "https://music.163.com/search/",
        'sec-ch-ua': "\"Chromium\";v=\"136\", \"Microsoft Edge\";v=\"136\", \"Not.A/Brand\";v=\"99\"",
        'sec-ch-ua-mobile': "?0",
        'sec-ch-ua-platform': "\"Windows\"",
        'sec-fetch-dest': "empty",
        'sec-fetch-mode': "cors",
        'sec-fetch-site': "same-origin",
        "Cookie": "自己去网易云提取吧。jpg"
    }
    return headers
# 搜索返回json
def searcht(name :str):
    s = json.dumps({
        "hlpretag": "<span class=\"s-fc7\">",
        "hlposttag": "</span>",
        "s": f"{name}",
        "type": "1",
        "offset": "0",
        "total": "true",
        "limit": "10",
        "csrf_token":f"{csrftoken}"
    })
    params, encSecKey = weapi_encrypt(s)
    d = buildreqdata(params, encSecKey)
    res = requests.post(f"https://music.163.com/weapi/cloudsearch/get/web?csrf_token={csrftoken}",headers=setheader(), data=d)
    return res.json()
# 传入id返回歌的数据里面有下载地址
def getmusic(id:str):
    s = json.dumps({
    "ids": f"[{id}]",
    "level": "exhigh",
    "encodeType": "ldac",
    "csrf_token": csrftoken
    })
    params, encSecKey = weapi_encrypt(s)
    d = buildreqdata(params, encSecKey)
    res=requests.post(f"https://music.163.com/weapi/song/enhance/player/url/v1?csrf_token={csrftoken}",headers=setheader(), data=d)
    return res.json()


