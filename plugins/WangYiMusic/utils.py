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
        "Cookie": "NMTID=00OOYbPCWf9rXQ8rkc2kFugvU4hdJoAAAGS06lFVQ; _ntes_nnid=cf1f2a0551e12f957e0a284a16d608e0,1730127937521; _ntes_nuid=cf1f2a0551e12f957e0a284a16d608e0; WM_TID=hsm04qf7wwhAUVFAQBPGDeZaiJZx5eL7; WEVNSM=1.0.0; WNMCID=kqnbhs.1730127939762.01.0; sDeviceId=YD-jb%2BIGzU9Ty9EUxRRQFfCSada2IJ2Is18; ntes_utid=tid._.mHP3mHMDxR9EAlFEFEbTTfcOzIY2N9gm._.0; Hm_lvt_28d7928d51823cf205a887c786b87efc=1730729174; __snaker__id=zY2ukqOPg8PmL4nx; _ga=GA1.1.1147274169.1744381565; Qs_lvt_382223=1744381565%2C1744381576%2C1744382761%2C1744576907; Qs_pv_382223=8727032091508127%2C495054081530949060%2C4593808876343592400%2C2871893747296762000%2C2955159756387013600; _ga_C6TGHFPQ1H=GS1.1.1744576907.2.0.1744576907.0.0.0; _clck=1u6ob5j%7C2%7Cfv1%7C0%7C1927; __remember_me=true; ntes_kaola_ad=1; _iuqxldmzr_=32; JSESSIONID-WYYY=bni%2BBygCcGznMvF%5CnlHqIcmmNXB4g%5CKYt32N%2FoRmRWx4vlHY6uPRuR2DHy5mePtm%5CkKiHV8CHacXhTfhDaIaZFnNm1gho5o%2BiIwnu9hm9KYJ3IjBhQbhmQfoAStlSYQ2HGfmmwt%2BmA4ltQT4d4ejCH%2BzWVW6rcVDyuuYJwD6aFcWwcuQ%3A1748526504007; WM_NI=kJEUMFc6d1rcVaiy89FxqHCDGjHYpgMeW1GKvHmUc3sCZqxHROgHliEyBgUh4LpNxrfKGNYO2VOXmbXouSuRZ5T1c60uYvWfIIghfdpLvISaEzXP6WqX0cpfYDP9i4LbZkE%3D; WM_NIKE=9ca17ae2e6ffcda170e2e6eeacd33cf492baa8d37c898e8aa6d54e979e9e86cb6ea9ba00a9b36a8ef18cb2cc2af0fea7c3b92aa9968bb6d04ea1eaacd2f16996bfa79abb3386a8bfa9c25085b688baf36d8fbb99d2c641b698a3b6e239b19d9692fb449291a9a9ef6387a78e88d864f894c0affb7aa69589afdc45f28a81b9f06a9cb1bbb4f85a9aeb9cd2e23ef4ab9797d66e8b97a989e570b494baaad26b87a698b1b55db3880084f36e9b9ca0daec4090be968cc837e2a3; gdxidpyhxdE=wADqSKj%2BlyLtjHdTVhr2g1ac1ooYrOVeEGBjkMg%5Cg3dqs%2BArfC1%5CjTC1NXW5D5G0Ok44c6KgCwY8VC3ufPVhOMUElXdMqbA40Lzw3j6Cn%5C46h6ElZXZtg8cfgf3LEhkYlr%5CuTOOEIko5wytyvZf4Ofodir%2BPdHE%2BXXdfZr%2B3h5%5CfWyyx%3A1748525607266; __csrf=f16087fc30cf7f7f7525edaff7b6f394; MUSIC_U=00E68891D31C4ABE9DD4805A2F3275265E4D2E2978389FDF1DD4DB0923A0B75F9A9A7DAC79FB112E6A23A5DE9F64CD04BC600FDC80056AC45C458C802F73C6DA181903BADF50DCDC3B403BBF06ABA3E3F13BC1006FB8BA728CDF9FD5FC65841789A2D11548225C19E8059FA684CF2502760FDC95313238E8EC4172F94FCE11C9E6B650542E32EA4422E02C206746F3B96E2CA7B8914982C1A86CBAFCBEDFF5F799F12B48C1FC65EA9C35C7BB981831F3B8CD274977329B6713BF8D7F2C369A0053838EDE5A92B4F590A31F77B8F04D0407B7BFD0E5BBD732E59195E6C8353B57BD97CB51FE089A8661221C8577C1C008B0A9CF3DDF79AD598C4D55469958FE4B81D0D9E41E375877B34EA37248371A05F2462782D3E821A0962F8F8476C2EA00D3E8DE264064CE64D04151B439F79808ED278C221BEF697079C11A93EEA7E9F2833B4A90EC8DDDF1C18C2E23097E610B77CEB580CF4A8388F6468C0D9CB91E4713"
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

# a=searcht("喜欢你")
# # print(a['result']["songs"][0]["id"])
# print(len(a['result']['songs']))
# for song in a['result']['songs']:
#     print(song["id"],song["name"],song["ar"][0]["name"])
# print("1".isdigit())
a=getmusic(213213)
if a["data"][0]["url"]!=None:
    print(a["data"][0]["url"])