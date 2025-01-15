from datetime import date


class CommonUtil:
    @staticmethod
    def get_avatar(user_id):
        return f'https://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640'

    @staticmethod
    def calculate_current_day():
        """获取当前日期，格式为YYYY-MM-DD"""
        return date.today().strftime("%Y-%m-%d")

    @staticmethod
    def bytes_to_long(bytes):
        """将字节数组转换为长整型"""
        return int.from_bytes(bytes, byteorder='big')