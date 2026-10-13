import random

LOCAL_VERIFICATION_QUESTIONS = [
    {"question": "中国的首都是哪里？", "correct_answer": "北京", "incorrect_answers": ["上海", "广州", "深圳"]},
    {"question": "一年有多少个月？", "correct_answer": "12", "incorrect_answers": ["10", "11", "13"]},
    {"question": "一周有多少天？", "correct_answer": "7", "incorrect_answers": ["5", "6", "8"]},
    {"question": "太阳从哪个方向升起？", "correct_answer": "东方", "incorrect_answers": ["西方", "南方", "北方"]},
    {"question": "水的化学式是什么？", "correct_answer": "H2O", "incorrect_answers": ["CO2", "O2", "NaCl"]},
    {"question": "地球上最大的海洋是哪个？", "correct_answer": "太平洋", "incorrect_answers": ["大西洋", "印度洋", "北冰洋"]},
    {"question": "哪个颜色是彩虹的第一种颜色？", "correct_answer": "红色", "incorrect_answers": ["橙色", "黄色", "绿色"]},
    {"question": "人体有多少个心脏？", "correct_answer": "1", "incorrect_answers": ["2", "3", "4"]},
    {"question": "哪个季节最热？", "correct_answer": "夏天", "incorrect_answers": ["春天", "秋天", "冬天"]},
    {"question": "哪个是最大的行星？", "correct_answer": "木星", "incorrect_answers": ["土星", "天王星", "海王星"]},
    {"question": "中国的国旗上有几颗星？", "correct_answer": "5", "incorrect_answers": ["3", "4", "6"]},
    {"question": "哪个是哺乳动物？", "correct_answer": "狗", "incorrect_answers": ["鱼", "鸟", "蛇"]},
    {"question": "哪个是水果？", "correct_answer": "苹果", "incorrect_answers": ["胡萝卜", "土豆", "洋葱"]},
    {"question": "哪个是交通工具？", "correct_answer": "汽车", "incorrect_answers": ["桌子", "椅子", "床"]},
    {"question": "哪个是颜色？", "correct_answer": "蓝色", "incorrect_answers": ["数字", "字母", "符号"]},
    {"question": "以下哪个不属于行星？", "correct_answer": "月亮", "incorrect_answers": ["地球", "火星", "金星"]},
    {"question": "以下哪个不属于水果？", "correct_answer": "胡萝卜", "incorrect_answers": ["苹果", "香蕉", "橙子"]},
    {"question": "以下哪个不属于交通工具？", "correct_answer": "房子", "incorrect_answers": ["汽车", "飞机", "火车"]},
    {"question": "以下哪个不属于颜色？", "correct_answer": "数字", "incorrect_answers": ["红色", "蓝色", "绿色"]},
    {"question": "以下哪个不属于哺乳动物？", "correct_answer": "鱼", "incorrect_answers": ["猫", "狗", "牛"]},
    {"question": "以下哪个不属于蔬菜？", "correct_answer": "苹果", "incorrect_answers": ["白菜", "萝卜", "黄瓜"]},
    {"question": "以下哪个不属于鸟类？", "correct_answer": "狗", "incorrect_answers": ["麻雀", "鸽子", "燕子"]},
    {"question": "以下哪个不属于金属？", "correct_answer": "木头", "incorrect_answers": ["铁", "铜", "铝"]},
    {"question": "以下哪个不属于饮料？", "correct_answer": "米饭", "incorrect_answers": ["水", "茶", "咖啡"]},
    {"question": "以下哪个不属于学习用品？", "correct_answer": "电视", "incorrect_answers": ["笔", "本子", "橡皮"]},
    {"question": "以下哪个不属于运动项目？", "correct_answer": "睡觉", "incorrect_answers": ["跑步", "游泳", "篮球"]},
    {"question": "以下哪个不属于季节？", "correct_answer": "星期", "incorrect_answers": ["春天", "夏天", "秋天"]},
    {"question": "以下哪个不属于方向？", "correct_answer": "上下", "incorrect_answers": ["东", "南", "西"]},
    {"question": "以下哪个不属于数字？", "correct_answer": "字母", "incorrect_answers": ["1", "2", "3"]}
]


def random_challenge() -> dict:
    """从本地题库随机抽一道题，选项顺序打乱。"""
    item = random.choice(LOCAL_VERIFICATION_QUESTIONS)
    options = item["incorrect_answers"] + [item["correct_answer"]]
    random.shuffle(options)
    return {
        "question": item["question"],
        "correct_answer": item["correct_answer"],
        "options": options,
    }
