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
        "Cookie": "NMTID=00OOYbPCWf9rXQ8rkc2kFugvU4hdJoAAAGS06lFVQ; _ntes_nnid=cf1f2a0551e12f957e0a284a16d608e0,1730127937521; _ntes_nuid=cf1f2a0551e12f957e0a284a16d608e0; WM_TID=hsm04qf7wwhAUVFAQBPGDeZaiJZx5eL7; WEVNSM=1.0.0; WNMCID=kqnbhs.1730127939762.01.0; sDeviceId=YD-jb%2BIGzU9Ty9EUxRRQFfCSada2IJ2Is18; ntes_utid=tid._.mHP3mHMDxR9EAlFEFEbTTfcOzIY2N9gm._.0; Hm_lvt_28d7928d51823cf205a887c786b87efc=1730729174; __snaker__id=zY2ukqOPg8PmL4nx; _ga=GA1.1.1147274169.1744381565; Qs_lvt_382223=1744381565%2C1744381576%2C1744382761%2C1744576907; Qs_pv_382223=8727032091508127%2C495054081530949060%2C4593808876343592400%2C2871893747296762000%2C2955159756387013600; _ga_C6TGHFPQ1H=GS1.1.1744576907.2.0.1744576907.0.0.0; _clck=1u6ob5j%7C2%7Cfv1%7C0%7C1927; __remember_me=true; _iuqxldmzr_=32; MUSIC_U=000F5E30BD0A39AE3AE2D68FB12FE63FE31843821B74CEB22C6AFC088BA41B44DF3CAB30F49C59DA2189D60AFA7BC1B5BA81A691CB63600BA94D90D2D4F3CB0F239F6CC059DEA23C10DF842441177D7515FFBEC6C7777A75618013129267132C52D591D27CDEDBDACAE3B7D9B1DF628886DC34211F4E709A29DDBC52DEF1B8272A3241A7F28ECF6C2D406261B81877918D54BC452084279CAF6A796500F2DB194F9E5FA605D4E2F917CE96E0C4FA9D65AE67CB6748652275C023011E5600E18DA520441B72764E8AB1240650FABBCF3048240CA266EE680B75480EAC2DA393B1AAB670616523C05DCF9DDD71E845BFF460269D9DEF3E602121936610999FF1F64BD9C2459D0C00239D7F1EADF7567780AB7A3396B28A3A2B228E5CA5D7417C7EA97269AB041BF3FF0DB8D5BE369B9576AC7D8B2C13206DE889AC016A42D0E1FFE4AAEE2DA5DEDBF2E6F5D780C16616D1CFA5B5FEE8F5DB837B6A26B5DF6F4C4109; __csrf=7d327f98beb7cb91ebc9ad1fd50f4d19; ntes_kaola_ad=1; gdxidpyhxdE=fJRV%2BTUjx3w8%2Fk1JMbp8qu9RoNLA8S%5CVbM%5CnJQLolngpXJaBjnCZ2mseHmohOnIae0PDDS5hN%2F1tLfjkB0oUwJa9jNxfKZwiTXVGTadoMNYCxU926j5%5CPl%5CdXCrVji7yM3KbNgAgOxJu7XeJdrrLnGPfRd%5CITv5hbiLSDamwtsMgaT8S%3A1747842175064; WM_NI=1Izn9JkdctBinAJyc01ndmSYi08fDzcTV6uz1v5BYSGNp3%2B3CsEudNn6n3voLBszCFPlM5wFFd82qU4lHeG7kXdMIOSNq6MhvUNMp6KC3qq59JiN%2FtIbHiGERsG3gJPWYVg%3D; WM_NIKE=9ca17ae2e6ffcda170e2e6eed6cb25b3aeb7b8d76792ef8ba3c15a829a8f86c66e90b0e5d3e26d91b8e58dd62af0fea7c3b92af397b68fbc42a9bcad95c163ae9f869ae23cf3b2ac8ed253abf0f887c772a18c9ad7f872f3aafba3dc598592c0d4aa3981ad8b96e746aeeaa2d4f565909ebd9bc24e94b483d6f64e86bcf8d3f45ff8b48886c63da988acaecc459192bfd7d064b68b82aef33e899dfd92f43ffb96a7afae808bbe888ac64f92ad89d4e642948997b7e637e2a3; JSESSIONID-WYYY=hfeHiNvw%2BIGCVPWlw71dKZbKORbh%2Ffu5XBMj1qpEpsJph5l5J0bd3KIX%2FRj%5C2ostXwhF5CxwciFyXWI1kHv2%2BWxhZioo3hpkw2bQA50lESz080lqRz%5CUHgUj0lDDGdO%2F%2ByH%2BJ96EKjv%5CF%5CYcVbxEb8x05baB%5CzthS%5CidgpnhMkdjqSh%5C%3A1747917420748; playerid=17334153"
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


