from ncatpy.message import GroupMessage

HTTP_CAT_BASE_URL = "https://http.cat/"
HTTP_STATUS_CODES = {
    100: "100", 101: "101", 102: "102", 103: "103",

    200: "200", 201: "201", 202: "202", 203: "203", 204: "204", 206: "206", 207: "207", 208: "208", 214: "214",
    226: "226",

    300: "300", 301: "301", 302: "302", 303: "303", 304: "304", 305: "305", 307: "307", 308: "308",

    400: "400", 401: "401", 402: "402", 403: "403", 404: "404", 405: "405", 406: "406", 407: "407", 408: "408",
    409: "409", 410: "410", 411: "411", 412: "412", 413: "413", 414: "414", 415: "415", 416: "416", 417: "417",
    418: "418", 420: "420", 421: "421", 422: "422", 423: "423", 424: "424", 425: "425", 426: "426", 428: "428",
    429: "429", 431: "431", 444: "444", 450: "450", 451: "451", 495: "495", 496: "496", 497: "497", 498: "498",
    499: "499",

    500: "500", 501: "501", 502: "502", 503: "503", 504: "504", 506: "506", 507: "507", 508: "508", 509: "509",
    510: "510", 511: "511", 521: "521", 522: "522", 523: "523", 525: "525", 530: "530", 599: "599"
}


class HttpCat:

    async def http_cat(self, input: GroupMessage):
        """
        处理 HTTP Cat 功能
        """
        message_content = input.raw_message
        if not message_content.isdigit():
            return

        status_code = int(message_content)
        if status_code in HTTP_STATUS_CODES:
            image_url = self.get_image_url(status_code)
            await input.add_image(image_url).reply()

    def get_image_url(self, status_code):
        """
        获取 HTTP Cat 图片的 URL
        :param status_code: HTTP 状态码
        :return: 图片的 URL，如果状态码无效则返回 None
        """
        status_description = HTTP_STATUS_CODES.get(status_code)
        return HTTP_CAT_BASE_URL + status_description
