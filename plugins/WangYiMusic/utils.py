import base64
import json
import requests
from Cryptodome.Cipher import AES, PKCS1_OAEP
from Cryptodome.PublicKey import RSA
from Cryptodome.Util.Padding import pad
from common.constants.HMMT import HMMT

Aeskey = "0CoJUm6Qyw8W8jud"
Aesiv = "0102030405060708"
modulushex = "00e0b509f6259df8642dbc35662901477df22677ec152b5ff68ace615bb7b725152b3ab17a876aea8a5aa76d2e417629ec4ee341f56135fccf695280104e0312ecbda92557c93870114af6c9d05c4f7f0c3685b7a46bee255932575cce10b424d813cfe4875d3e82047b97ddef52741d546b8e289dc6935b3ece0462db0a22b8e7"
exponenthex = "010001"
csrftoken = "7d327f98beb7cb91ebc9ad1fd50f4d19"


def Aesencrypt(plain_text, key_str, iv):
    key = key_str.encode("utf-8")
    iv_bytes = iv.encode("utf-8")
    plain_data = plain_text.encode("utf-8")
    cipher = AES.new(key, AES.MODE_CBC, iv_bytes)
    ciphertext = cipher.encrypt(pad(plain_data, AES.block_size))
    return base64.b64encode(ciphertext).decode("utf-8")


def Rsaencrypt(sec_key, pub_key, modulus):
    reversed_key = sec_key[::-1]
    hex_key = "".join(format(ord(c), "x") for c in reversed_key)
    key_int = int(hex_key, 16)
    pub_int = int(pub_key, 16)
    mod_int = int(modulus, 16)
    result = pow(key_int, pub_int, mod_int)
    hex_result = format(result, "x")
    modulus_len = len(modulus.lstrip("0"))
    padding_len = max(0, modulus_len - len(hex_result))
    return "0" * padding_len + hex_result


def buildreqdata(params, enc_sec_key):
    return {"params": params, "encSecKey": enc_sec_key}


def weapi_encrypt(s: str):
    one = Aesencrypt(s, Aeskey, Aesiv)
    params = Aesencrypt(one, Aeskey, Aesiv)
    encSecKey = Rsaencrypt(Aeskey, exponenthex, modulushex)
    return params, encSecKey


def setheader():
    return {
        "User-Agent": HMMT.USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/x-www-form-urlencoded",
        "Origin": "https://music.163.com",
        "Referer": "https://music.163.com/",
        "Cookie": "__remember_me=true; MUSIC_U=00E68891D31C4ABE9DD4805A2F3275265E4D2E2978389FDF1DD4DB0923A0B75F9A9A7DAC79FB112E6A23A5DE9F64CD04BC600FDC80056AC45C458C802F73C6DA181903BADF50DCDC3B403BBF06ABA3E3F13BC1006FB8BA728CDF9FD5FC65841789A2D11548225C19E8059FA684CF2502760FDC95313238E8EC4172F94FCE11C9E6B650542E32EA4422E02C206746F3B96E2CA7B8914982C1A86CBAFCBEDFF5F799F12B48C1FC65EA9C35C7BB981831F3B8CD274977329B6713BF8D7F2C369A0053838EDE5A92B4F590A31F77B8F04D0407B7BFD0E5BBD732E59195E6C8353B57BD97CB51FE089A8661221C8577C1C008B0A9CF3DDF79AD598C4D55469958FE4B81D0D9E41E375877B34EA37248371A05F2462782D3E821A0962F8F8476C2EA00D3E8DE264064CE64D04151B439F79808ED278C221BEF697079C11A93EEA7E9F2833B4A90EC8DDDF1C18C2E23097E610B77CEB580CF4A8388F6468C0D9CB91E4713",
    }


def searcht(name: str):
    s = json.dumps(
        {
            "hlpretag": '<span class="s-fc7">',
            "hlposttag": "</span>",
            "s": name,
            "type": "1",
            "offset": "0",
            "total": "true",
            "limit": "10",
            "csrf_token": csrftoken,
        }
    )
    params, encSecKey = weapi_encrypt(s)
    d = buildreqdata(params, encSecKey)
    res = requests.post(
        "https://music.163.com/weapi/cloudsearch/get/web", headers=setheader(), data=d
    )
    return res.json()


def getmusic(id: str):
    s = json.dumps(
        {
            "ids": f"[{id}]",
            "level": "exhigh",
            "encodeType": "aac",
            "csrf_token": csrftoken,
        }
    )
    params, encSecKey = weapi_encrypt(s)
    d = buildreqdata(params, encSecKey)
    res = requests.post(
        "https://music.163.com/weapi/song/enhance/player/url/v1",
        headers=setheader(),
        data=d,
    )
    return res.json()


# a=searcht("喜欢你")
# # print(a['result']["songs"][0]["id"])
# print(len(a['result']['songs']))
# for song in a['result']['songs']:
#     print(song["id"],song["name"],song["ar"][0]["name"])
# print("1".isdigit())
a = getmusic(213213)
if a["data"][0]["url"] != None:
    print(a["data"][0]["url"])
