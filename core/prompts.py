import os

from core import assets_lib
from core.assets import user_image_variant


# 预置造型模板（角色，100×100）：兜底用。暂不接入 system（避免诱导模型自己画），
# 留作「分步生成」阶段按需注入的兜底素材。
_COSTUME_TEMPLATES = [
    ("人形角色", "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100' viewBox='0 0 100 100'><circle cx='50' cy='30' r='17' fill='#FFD7B5'/><circle cx='44' cy='28' r='3' fill='#333333'/><circle cx='56' cy='28' r='3' fill='#333333'/><path d='M44 38 Q50 44 56 38' stroke='#333333' stroke-width='2' fill='none'/><rect x='36' y='48' width='28' height='30' rx='8' fill='#4C97FF'/><rect x='31' y='50' width='8' height='20' rx='4' fill='#4C97FF'/><rect x='61' y='50' width='8' height='20' rx='4' fill='#4C97FF'/><rect x='40' y='78' width='8' height='18' fill='#5D4037'/><rect x='52' y='78' width='8' height='18' fill='#5D4037'/></svg>"),
    ("猫", "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100' viewBox='0 0 100 100'><polygon points='30,24 36,6 48,20' fill='#FFB84D'/><polygon points='70,24 64,6 52,20' fill='#FFB84D'/><circle cx='50' cy='50' r='28' fill='#FFB84D'/><circle cx='41' cy='46' r='4' fill='#333333'/><circle cx='59' cy='46' r='4' fill='#333333'/><path d='M45 58 Q50 62 55 58' stroke='#333333' stroke-width='2' fill='none'/><path d='M50 52 L50 62 M42 50 L33 48 M58 50 L67 48' stroke='#333333' stroke-width='1.5'/></svg>"),
    ("球", "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100' viewBox='0 0 100 100'><circle cx='50' cy='50' r='38' fill='#FF5252'/><ellipse cx='35' cy='33' rx='13' ry='8' fill='#FF8A80'/></svg>"),
    ("火箭", "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100' viewBox='0 0 100 100'><path d='M50 6 L66 40 L34 40 Z' fill='#EEEEEE'/><rect x='44' y='40' width='12' height='32' rx='3' fill='#BDBDBD'/><circle cx='50' cy='50' r='6' fill='#4C97FF'/><polygon points='40,72 46,82 50,74' fill='#FF5252'/><polygon points='60,72 54,82 50,74' fill='#FF5252'/><polygon points='42,72 50,76 58,72 50,92' fill='#FF7043'/></svg>"),
    ("怪物", "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100' viewBox='0 0 100 100'><rect x='22' y='30' width='56' height='46' rx='16' fill='#8BC34A'/><circle cx='38' cy='50' r='9' fill='#FFFFFF'/><circle cx='62' cy='50' r='9' fill='#FFFFFF'/><circle cx='38' cy='50' r='4' fill='#333333'/><circle cx='62' cy='50' r='4' fill='#333333'/><path d='M40 62 Q50 70 60 62' stroke='#333333' stroke-width='2' fill='none'/></svg>"),
    ("星星", "<svg xmlns='http://www.w3.org/2000/svg' width='100' height='100' viewBox='0 0 100 100'><polygon points='50,8 61,38 92,38 67,57 76,88 50,70 24,88 33,57 8,38 39,38' fill='#FFD54F'/></svg>"),
]

# 预置背景模板（舞台，480×360）：兜底用，暂未接入 system。
_BACKDROP_TEMPLATES = [
    ("天空草地", "<svg xmlns='http://www.w3.org/2000/svg' width='480' height='360' viewBox='0 0 480 360'><rect width='480' height='260' fill='#87CEEB'/><rect y='260' width='480' height='100' fill='#66BB6A'/><circle cx='400' cy='70' r='40' fill='#FFD54F'/><ellipse cx='120' cy='80' rx='55' ry='20' fill='#FFFFFF'/><ellipse cx='170' cy='92' rx='42' ry='15' fill='#FFFFFF'/></svg>"),
    ("夜晚", "<svg xmlns='http://www.w3.org/2000/svg' width='480' height='360' viewBox='0 0 480 360'><rect width='480' height='360' fill='#1A237E'/><circle cx='380' cy='80' r='34' fill='#FFF9C4'/><circle cx='368' cy='72' r='30' fill='#1A237E'/><circle cx='60' cy='50' r='2' fill='#FFFFFF'/><circle cx='140' cy='90' r='2' fill='#FFFFFF'/><circle cx='240' cy='40' r='2' fill='#FFFFFF'/><circle cx='320' cy='70' r='2' fill='#FFFFFF'/><circle cx='450' cy='60' r='2' fill='#FFFFFF'/><polygon points='0,270 120,170 240,270 360,190 480,270 480,360 0,360' fill='#283593'/></svg>"),
    ("室内", "<svg xmlns='http://www.w3.org/2000/svg' width='480' height='360' viewBox='0 0 480 360'><rect width='480' height='250' fill='#F5E6CA'/><rect y='250' width='480' height='110' fill='#B08968'/><rect x='300' y='50' width='120' height='100' fill='#B3E5FC'/><rect x='355' y='50' width='8' height='100' fill='#8D6E63'/><rect x='300' y='95' width='120' height='8' fill='#8D6E63'/><rect x='50' y='120' width='100' height='60' rx='6' fill='#8D6E63'/><rect x='50' y='200' width='180' height='50' fill='#6D4C41'/></svg>"),
    ("海洋", "<svg xmlns='http://www.w3.org/2000/svg' width='480' height='360' viewBox='0 0 480 360'><rect width='480' height='220' fill='#29B6F6'/><rect y='220' width='480' height='140' fill='#0277BD'/><circle cx='80' cy='60' r='30' fill='#FFF59D'/><path d='M0 235 Q60 220 120 235 T240 235 T360 235 T480 235' stroke='#B3E5FC' stroke-width='6' fill='none'/><path d='M0 280 Q60 265 120 280 T240 280 T360 280 T480 280' stroke='#81D4FA' stroke-width='6' fill='none'/></svg>"),
    ("城市", "<svg xmlns='http://www.w3.org/2000/svg' width='480' height='360' viewBox='0 0 480 360'><rect width='480' height='300' fill='#BBDEFB'/><rect y='300' width='480' height='60' fill='#90A4AE'/><rect x='30' y='160' width='70' height='140' fill='#78909C'/><rect x='120' y='100' width='90' height='200' fill='#90A4AE'/><rect x='230' y='180' width='60' height='120' fill='#78909C'/><rect x='310' y='120' width='80' height='180' fill='#90A4AE'/><rect x='410' y='200' width='60' height='100' fill='#78909C'/><rect x='45' y='180' width='18' height='18' fill='#FFF9C4'/><rect x='155' y='130' width='20' height='20' fill='#FFF9C4'/><rect x='345' y='150' width='20' height='20' fill='#FFF9C4'/></svg>"),
    ("森林", "<svg xmlns='http://www.w3.org/2000/svg' width='480' height='360' viewBox='0 0 480 360'><rect width='480' height='300' fill='#C8E6C9'/><rect y='300' width='480' height='60' fill='#4CAF50'/><rect x='40' y='180' width='20' height='120' fill='#795548'/><polygon points='10,200 50,90 90,200' fill='#388E3C'/><rect x='150' y='200' width='22' height='100' fill='#795548'/><polygon points='115,220 161,100 207,220' fill='#2E7D32'/><rect x='280' y='170' width='20' height='130' fill='#795548'/><polygon points='245,190 290,70 335,190' fill='#388E3C'/><rect x='380' y='210' width='18' height='90' fill='#795548'/><polygon points='350,230 389,120 428,230' fill='#2E7D32'/></svg>"),
    # 下面是「素材库缺题材」时用的通用室内场景（BUG-14 遗留问题的数据层解法）：
    # 官方背景只有 Room/Bedroom/Hall/Party/Refrigerator 这类，做商场、餐厅、店铺题材时
    # 模型只能就近借用不相关的背景，所以补几张能覆盖常见商业场景的模板。
    ("商店", "<svg xmlns='http://www.w3.org/2000/svg' width='480' height='360' viewBox='0 0 480 360'><rect width='480' height='360' fill='#FFF3E0'/><rect y='250' width='480' height='110' fill='#D7CCC8'/><rect x='30' y='60' width='180' height='14' fill='#8D6E63'/><rect x='30' y='120' width='180' height='14' fill='#8D6E63'/><rect x='30' y='180' width='180' height='14' fill='#8D6E63'/><rect x='45' y='40' width='16' height='20' fill='#EF5350'/><rect x='75' y='44' width='16' height='16' fill='#42A5F5'/><rect x='105' y='38' width='16' height='22' fill='#FFCA28'/><rect x='45' y='100' width='16' height='20' fill='#66BB6A'/><rect x='75' y='96' width='16' height='24' fill='#AB47BC'/><rect x='105' y='102' width='16' height='18' fill='#FF7043'/><rect x='45' y='160' width='16' height='20' fill='#26C6DA'/><rect x='75' y='164' width='16' height='16' fill='#EC407A'/><rect x='290' y='200' width='160' height='70' rx='8' fill='#A1887F'/><rect x='300' y='210' width='140' height='12' fill='#6D4C41'/><rect x='330' y='55' width='90' height='115' rx='6' fill='#B3E5FC'/><rect x='372' y='55' width='6' height='115' fill='#90A4AE'/></svg>"),
    ("餐厅", "<svg xmlns='http://www.w3.org/2000/svg' width='480' height='360' viewBox='0 0 480 360'><rect width='480' height='360' fill='#FFF8E1'/><rect y='265' width='480' height='95' fill='#D7CCC8'/><rect x='300' y='70' width='150' height='90' rx='6' fill='#A5D6A7'/><rect x='300' y='70' width='150' height='16' fill='#66BB6A'/><rect x='310' y='96' width='30' height='26' fill='#FFF176'/><rect x='350' y='96' width='30' height='26' fill='#FF8A65'/><rect x='390' y='96' width='30' height='26' fill='#4FC3F7'/><ellipse cx='110' cy='250' rx='55' ry='16' fill='#FFFFFF'/><rect x='104' y='250' width='12' height='30' fill='#8D6E63'/><ellipse cx='230' cy='272' rx='40' ry='12' fill='#FFFFFF'/><circle cx='230' cy='258' r='12' fill='#FFB74D'/><rect x='60' y='40' width='8' height='40' fill='#616161'/><circle cx='64' cy='86' r='14' fill='#FFE082'/><rect x='180' y='40' width='8' height='40' fill='#616161'/><circle cx='184' cy='86' r='14' fill='#FFE082'/></svg>"),
    ("商场中庭", "<svg xmlns='http://www.w3.org/2000/svg' width='480' height='360' viewBox='0 0 480 360'><rect width='480' height='360' fill='#ECEFF1'/><rect y='280' width='480' height='80' fill='#B0BEC5'/><rect x='20' y='60' width='120' height='220' fill='#CFD8DC'/><rect x='340' y='60' width='120' height='220' fill='#CFD8DC'/><rect x='20' y='100' width='120' height='30' fill='#90CAF9'/><rect x='20' y='170' width='120' height='30' fill='#90CAF9'/><rect x='340' y='100' width='120' height='30' fill='#90CAF9'/><rect x='340' y='170' width='120' height='30' fill='#90CAF9'/><rect x='200' y='250' width='80' height='10' fill='#78909C'/><path d='M200 250 L280 120' stroke='#78909C' stroke-width='8'/><circle cx='240' cy='70' r='24' fill='#FFF59D'/><rect x='232' y='70' width='16' height='40' fill='#B0BEC5'/></svg>"),
]


# 游览 / 剧情类场景包（按需注入，非游览类需求不加载）
_SCENE_TOUR = """
【游览 / 剧情类项目（例如"购物中心游览"）的标准结构，按这个来组织】
⚠️ 下面的背景名 Lobby、Beauty1 只是**结构占位示意**，不是真实素材；实际生成时背景必须换成文末【素材目录】里真实存在的名字（如 Hall、Room 1、Metro），禁止照抄占位名。
整体分四段，全部用广播串起来：
  段 1 标题页：背景 "Lobby"，一个 Title 角色显示欢迎语，Guide 角色在旁边招手；提示点击选项。
  段 2 选择路线：屏幕上并排三个选项角色（如 RouteA / RouteB / RouteC），各自 when clicked。
        **关键：点中一个后，三个选项必须一起消失。** 角色之间不能互相 hide，正确做法是"点谁谁广播，所有选项一起收"：
        - 每个选项角色的 when clicked 里只做两件事：记下路线 + broadcast 同一条消息（如 start_tour）。
        - 每个选项角色（包括被点的那个）都要再写一个 `when broadcast start_tour { hide; }`，收到广播后各自隐藏。
        - 例（RouteA / RouteB / RouteC 三个文件写法相同，只有 route 的值不同）：
              // RouteA/main.fractch
              when clicked {
                  route = 1;
                  broadcast start_tour;
              }
              when broadcast start_tour {
                  hide;
              }
        ⚠️ 绝对不要只在被点的那个角色里 hide——那样只有它自己消失，另外两个会一直留在屏幕上挡住后面所有场景。
  段 3 走路线 —— 重点：用广播同步，不要靠"各自算好时长"来对齐动作：
        - Guide 当导演，按顺序推进每一站，并用 broadcast_wait 等所有人做完：
              when broadcast start_tour {
                  if route == 1 {
                      broadcast_wait station1;
                      broadcast_wait station2;
                      broadcast_wait station3;
                  } else if route == 2 {
                      broadcast_wait food1;
                      broadcast_wait food2;
                      ...
                  }
                  broadcast end;
              }
        - 每一站由一条广播驱动，多个角色订阅同一条广播，各做各的事、天然同步：
              # NPC/main.fractch —— 换背景 + 讲解，背景只能由它一个人切
              when broadcast station1 {
                  backdrop "Beauty1";
                  say "Welcome to Beauty & Perfume!" for 3;
                  say "Press space to try a sample." for 3;
              }
              # Guide/main.fractch —— 带路 + 报站
              when broadcast station1 {
                  motion.glidesecstoxy(SECS: 1.5, X: -50, Y: -80);
                  say "This is the Beauty section." for 3;
              }
          broadcast_wait 会等这一站所有角色的动作和台词都结束才推进。
        - 每站内容要够：NPC 至少两句 say（各 3 秒左右），再安排一个小互动。
  段 4 结束：最后一站做完后 backdrop "Lobby"，NPC 说结束语，Guide 广播 end，
        **并在这里把状态变量复位（playing = 0;）**，三个选项角色在 when broadcast end 里 show 出来。

写这条要求的代码时注意：
  - 三条路线要各有各的背景组合、广播名和 NPC 台词，不要三条路线内容雷同。
  - 选项角色必须靠广播"一起消失"：每个选项角色都要写 `when broadcast start_tour { hide; }`。
  - 用户选的路线写进 Stage/main.fractch 里的全局变量 route。
  - **状态变量必须能复位**：playing 这类标志，结束时要写回初值（playing = 0;）。
  - 主角（Guide）不能静止超过 5 秒：任何一次纯 wait 都不要超过 3 秒。
  - 台词里不要提示做不到的操作：只有真的写了 when key / when clicked 处理，才允许提示按键。
  - 分数之类的状态数值用变量监视器显示（watch var "score" at 10,10;）。
"""


# 点击类小游戏场景包（按需注入）
_SCENE_GAME = """
【点击类小游戏（打地鼠、点气球等）】用轮询鼠标，一个角色一个脚本完成 冒头→等待被点→收回：
   when flag {
       set_x -120;                     # 这个角色固定的位置
       set_y -80;
       hide;
       forever {
           if playing == 1 {           # playing 是 Stage/main.fractch 里声明的全局变量
               hit = 0;                # hit 是本角色私有变量，要在文件顶部写 var hit = 0;
               wait random(0.5, 2);    # 躲起来一段时间
               show;
               repeat 20 {             # 约 0.6 秒的可点击窗口，不能 show 完马上 hide
                   if hit == 0 && sensing.mousedown() && sensing.touchingobject(TOUCHINGOBJECTMENU: "_mouse_") {
                       hit = 1;        # 防止一次点击被重复计分
                       score += 1;     # 加分只能用 +=
                       hide;
                   }
                   wait 0.03;
               }
               hide;
           }
           wait 0.05;
       }
   }
"""


# 让 LLM 直接输出 fractch 项目文件清单（多文件），程序写盘后交给 fractch 打包
_SYSTEM_PROMPT_CORE = """
你是一个 Scratch 项目自动化生成 Agent，用 FRACTCH 语言编写项目。
用户会描述一个游戏或功能，你需要输出一份「文件清单」，程序会把这些文件写成 fractch 工程并打包为 .sb3。

【输出格式】只输出 JSON 本体，不要 Markdown 代码块，不要任何解释文字：
{
  "files": [
    {"path": "Stage/main.fractch", "content": "舞台的 fractch 代码"},
    {"path": "Stage/assets/Lobby.svg", "content": "<svg ... 第 1 张背景的源码 ...>"},
    {"path": "Guide/main.fractch", "content": "Guide 角色的 fractch 代码"},
    {"path": "Guide/assets/idle.svg", "content": "<svg ... 第 1 套造型的源码 ...>"}
  ]
}

【工程结构规则】：
1. 每个角色是一个文件夹，文件夹名就是角色名；舞台固定是 Stage 文件夹。
2. 每个角色（含 Stage）都必须有一个 main.fractch，放该角色的全部代码。
3. 造型/背景是 assets/ 下的 .svg 文件：在 main.fractch 顶部用
   costume "名字" file "assets/xxx.svg";   声明角色造型；
   舞台的背景用 backdrop "名字" file "assets/xxx.svg" center 240,180;  声明，
   （center 240,180 是舞台中心，必须写，否则背景会向右下偏移、左上角留白）。
   并在 files 数组里给出对应 .svg 文件的完整源码，路径要和声明里的 file 完全一致。
4. 角色名只能用英文字母、数字、下划线，必须各不相同，且不能叫 stage（不分大小写）。
5. 造型名、背景名也只能用英文字母、数字、下划线，代码里引用要完全一致（区分大小写）。
6. files 里每个 path 都必须是相对路径，不要出现绝对路径或 ".."。
7. 角色数量按需求定：游览/剧情类建议 4~6 个；小游戏建议 2~4 个。角色之间用广播协作。

【变量与列表】：
- 写在 Stage/main.fractch 顶部的 var 是全局变量，所有角色共享；写在某角色 main.fractch 顶部的 var 是该角色私有。
- 分数、生命值、路线等共享状态一律放 Stage。
- 变量声明：var score = 0;  列表声明：var items = [];
- 赋值/自增：score = 5;  score += 1;  score -= 1;  score *= 2;
- 列表操作：append(items, v);  delete(items, i);  insert(items, i, v);  clear(items);
  item(items, i);  items.length;  hasItem(items, v);  indexOf(items, v);
- 变量监视器（在舞台上显示数值）用声明：watch var "score" at 10,10;

【素材：一律用官方素材库，禁止自己画 SVG】
程序内置 Scratch 官方素材库（数百个专业设计的角色/背景/音效），直接引用即可，效果远好于自己画。
规则：
1. **先根据需求确定题材**，再到文末【素材目录】里对应题材挑角色和背景。
   同一项目尽量选同一题材、画面协调的素材；素材名必须真实存在于目录，禁止凭记忆编造。
2. 造型/背景一律用 `@official:<素材名>` 引用，**不要自己写 SVG**：
       {"path":"Guide/assets/cat-a.svg","content":"@official:cat-a"},
       {"path":"Stage/assets/Galaxy.png","content":"@official:Galaxy"}
   ★ 用官方素材的角色**不要在 main.fractch 里写 costume / backdrop 声明**，程序会自动补上（含正确旋转中心）。
   ★ 素材名**可以含空格**（如 City Bus、Room 1），直接照抄目录里的名字，`@official:City Bus` 是合法的；不要因为名字含空格就自己去画 SVG。
3. 代码里 `costume "cat-a";` / `backdrop "Galaxy";` 切换，名字就是素材名。
4. 一个角色的造型通常成套（如 Cat 有 cat-a / cat-b），想用动画就把它们都引用（没被代码引用的会被剪掉）。
5. 目录里确实没有合适素材时，才按下面兜底自己画（用最简单几何图形）。

【SVG 兜底（仅当官方素材库确实没有、必须自己画时，用最简单几何图形）】
- 合法 XML：单根 <svg>、标签闭合、属性值一律单引号。
- 根节点含 xmlns='http://www.w3.org/2000/svg' + width/height/viewBox。
- 角色造型 width='100' height='100' viewBox='0 0 100 100'；舞台背景 width='480' height='360' viewBox='0 0 480 360'。
- 只用 svg/g/rect/circle/ellipse/line/polyline/polygon/path/text，用 fill 上色（#RRGGBB 或颜色名）。
  禁止 style/script/image/use/defs/filter/渐变/外部引用。

【FRACTCH 语法：只能用下面这些，不要自己发明名字】
▲ 注释：// 或 /* */ 都可以。
▲ 事件帽子（脚本开头）：
  when flag { ... }                # 绿旗
  when clicked { ... }             # 角色被点击
  when key space { ... }           # 按键：space / "up arrow" / "left arrow" / "right arrow" / "a"~"z"
  when broadcast X { ... }         # 收到广播 X（消息名是标识符，不用引号）
  when clone { ... }               # 作为克隆体启动时
  when backdrop "背景名" { ... }   # 背景切换到某张时触发
▲ 运动：
  move 10;  turn 15;  turn_left 15;  turn_right 15;  point 90;   # point=面向方向
  goto 0, 0;  set_x 0;  change_x 10;  set_y 0;  change_y 10;
  # ▲ move / turn / change_x / change_y 后面**只能跟数字字面量**：
  #   写 change_x -速度 这类变量或算式会直接报语法错（expected a number）；
  #   需要按变量改位置时，用 if/else 分档写不同字面量。
  motion.glidesecstoxy(SECS: 1, X: 0, Y: 0);    # 滑行到 x,y，用 1 秒
  motion.ifonedgebounce();                       # 碰到边缘就反弹（无参也要写括号）
  motion.xposition();  motion.yposition();  motion.direction();
  motion.pointtowards(TOWARDS: "_mouse_");       # 面向鼠标；面向角色填角色名
  motion.setrotationstyle(STYLE: "left-right");  # 旋转方式：left-right / all around / don't rotate
▲ 外观：
  say "文本";  say "文本" for 2;  think "文本";  think "文本" for 2;  ask "问题";
  set_size 100;  change_size 10;  show;  hide;
  costume "造型名";  next_costume;   backdrop "背景名";  next_backdrop;
  set_effect ghost to 50;  change_effect brightness by 25;  clear_effects;
  go_front;  go_back;  go_forward 1;  go_backward 1;
▲ 广播与控制：
  broadcast 消息名;  broadcast_wait 消息名;
  wait 1;  repeat 10 { ... }  forever { ... }  until 条件 { ... }  while 条件 { ... }
  if 条件 { ... } else if 条件2 { ... } else { ... }
  clone;  clone "角色名";  delete_clone;
  stop all;  return;    # return = 停止这个脚本
▲ 侦测：
  sensing.mousedown();  sensing.mousex();  sensing.mousey();
  sensing.keypressed(KEY_OPTION: sensing.keyoptions("space"));
  sensing.touchingobject(TOUCHINGOBJECTMENU: "_mouse_");   # 碰到鼠标；碰到角色填角色名
  sensing.distanceto(DISTANCETOMENU: "_mouse_");
  reset_timer;  sensing.timer();
  answer;                            # ask 之后取用户输入
  sensing.setdragmode(DRAG_MODE: "draggable");   # 允许拖动角色：draggable / not draggable
  sprites["角色名"].x   sprites["角色名"].y   sprites["角色名"].direction   # 读别的角色的属性
  sprites["角色名"].size   sprites["角色名"].costume_name   sprites["_stage_"].backdrop_name
  sprites["角色名"].vars["变量名"]   # 读别的角色的某个变量
▲ 画笔（文件顶部先写 use "pen";）：
  pen_down;  pen_up;  pen_clear;  stamp;
▲ 音乐（文件顶部先写 use "music";）：
  music.playNoteForBeats(NOTE: 60, BEATS: 0.5);   # 音高 0~130，持续 0.5 拍
  music.playDrumForBeats(DRUM: 1, BEATS: 0.25);   # 鼓号 1~18
  music.setInstrument(INSTRUMENT: 1);             # 乐器 1~21
  music.restForBeats(BEATS: 0.25);
  music.setTempo(TEMPO: 60);
  sound.setvolumeto(VOLUME: 100);  sound.changevolumeby(VOLUME: -10);
▲ 取值与运算：
  random(1, 10);  length("字符串");  contains("子串", "字符串");
  round(x) abs(x) sqrt(x) sin(x) cos(x) tan(x);
  运算：+ - * / %   比较 == != < > <= >=   逻辑 &&(且) ||(或) !(非)   字符串连接 ++
  # 注意：字符串连接是 ++（不是 &）；逻辑与或非是 && || !（不是 and/or/not）
▲ 变量（var 声明写在文件顶部、所有事件块之外）：
  var score = 0;                  # 声明；写在 Stage/main.fractch 里就是全局变量，所有角色都能用
  score = 5;                      # 设值
  score += 1;                     # 加 1
  score -= 1;                     # 减 1
  score *= 2;                     # 复合赋值（还有 /= 等）
  # 自增只能用 += 或 -=（没有 ++ / -- 这种写法）
▲ 列表（var 声明成数组形式，也写在文件顶部）：
  var items = [];                 # 声明空列表；写在 Stage/main.fractch 里就是全局列表
  append(items, 值);              # 添加到末尾
  insert(items, i, 值);           # 插入到第 i 位
  replace(items, i, 值);          # 替换第 i 项
  delete(items, i);               # 删除第 i 项
  clear(items);                   # 清空整个列表
  items.length;                   # 长度
  hasItem(items, 值);             # 是否包含
  indexOf(items, 值);             # 返回位置，0 表示不存在
  item(items, i);                 # 取第 i 项
▲ 自定义积木（把重复的逻辑抽出来，两个帽子做同一件事就用它，别把同样的代码抄两遍）：
  def @名字() { ... }             # 定义，无参数
  def @名字(a, b) { ... }         # 带参数：过程体里直接用 a、b
  @名字();                        # 调用
  # 例：
  # def @pick_beauty() {
  #     route = 1; playing = 1; broadcast start_tour; hide;
  # }
  # when clicked { @pick_beauty(); }
▲ 语法要点：
  比较必须用 ==（不能用单个 =）；每条语句以分号结尾（分号可省略但建议写）；
  字符串一律用双引号；消息名（广播）用标识符，不用引号；
  引用别的角色时名字要和文件夹名完全一致（区分大小写）；
  costume / backdrop 里的名字要和 costume 声明的名字完全一致（区分大小写）；
  无参数的积木（如 motion.ifonedgebounce()）也必须写括号；
  带参数的运动积木**必须带 motion. 前缀**（motion.setrotationstyle(...) / motion.pointtowards(...) /
  motion.glidesecstoxy(...)）；裸写 setrotationstyle(...) 会被当成未知积木；
  不要用播放音频文件的积木（音乐积木 music.playNoteForBeats 等可以用）。

【可靠性规则：下面是"能编译但玩起来会坏"的坑，必须遵守】
1. 同一个角色只允许有一个脚本控制 show / hide。两个脚本各管一半（例如 when clicked 里 hide、
   另一个 forever 里 show）会互相打架，导致角色被打中后长时间不再出现。
2. HUD / 文字提示角色：放在舞台四角（如 set_x -190, set_y 150）并 set_size 50~70。
   它们默认在图层最上面，绝不能放在其他角色所在的区域，否则会把鼠标点击挡掉。
3. 背景音乐：用一个角色的 forever 循环反复 music.playNoteForBeats 一小段旋律，用 Stage 里的全局变量
   music_on 控制开关；循环里必须有 wait（如 wait 0.5;），否则音符会糊成一团。
4. 一段流程结束要能收尾、也能重来：用全局变量（playing / route 等）记录状态，
   所有循环靠 if playing == 1 自己退出，不要用 return 去停别的脚本。
   HUD 里刷新文字的 forever 也要包在 if playing == 1 里，否则结束后它会不停覆盖结束语。
5. 任何一次纯 wait 都不要超过 3 秒；需要长时间停留就拆成多段（主角不能静止超过 5 秒）。
6. 同一个角色里，同款帽子积木只能有一个：when flag / when clicked / when key X / when broadcast X 各最多写一次。
   需要按路线或场景分流时，「合并」成一个帽子，在里面用 if / else if / else 区分
   （例如 when key space 只写一个，里面判断 route == 1 / route == 2 / route == 3）。
   ⚠️ 是合并，不是删除：原来三条路线各有一个 when clicked，合并后仍然要有 when clicked，只是变成一个
   when clicked 里分三种情况。绝对不要为了让帽子唯一而把交互方式整个删掉——删掉之后用户点击、
   按键就完全没反应了。
7. 不要声明用不到的变量 / 列表：写了 var x = 0; 或 var y = []; 就一定要有地方真的用它，
   否则删掉这条声明，别留死代码。
8. 输出前自检一遍：
   - 每个 forever 里都有 wait，不会卡死；
   - 同一个角色里没有重复的同款帽子积木（when flag / when clicked / when key / when broadcast 各只有一个）；
   - 没有声明了却全程没用到的变量 / 列表；
   - 加分的变量和最后显示分数的变量是同一个（都在 Stage/main.fractch 里声明为全局变量）；
   - 每次加分都写成 `score += 1;`，全文没有出现过 `change xxx =` 这种写法；
   - 每个 `broadcast X;` 都有对应的 `when broadcast X { ... }` 接收，没有发出去没人收的广播；
   - 引用别的角色时名字和文件夹名完全一致；
   - costume / backdrop 里写的名字和 costume 声明的名字完全一致；
   - 用到的变量都用 var 声明过、用到的列表都用 var = [] 声明过；
   - 游览类项目：三条路线都能真正走完并在结尾回到大厅；选项角色被选中后都 hide 了；
   - 台词里提到的操作（按某个键、点击某个角色）都真有对应的 when key / when clicked 处理，
     而且对三条路线都成立；自动推进的地方不要写"按 X 继续"这种提示；
   - 被点中的目标过一会儿还能再次出现，可以反复得分；
   - 所有角色的活动范围在 x: -200~200、y: -140~140 之内，不会跑到舞台外。
"""


def _select_scene_pack(user_prompt: str) -> str:
    """根据用户需求关键词，选择要按需注入的场景包文本。"""
    p = (user_prompt or "").lower()
    parts = []
    if any(k in p for k in ("游览", "路线", "导游", "剧情", "参观", "讲解", "导览", "场景", "tour", "guide")):
        parts.append(_SCENE_TOUR)
    if any(k in p for k in ("打地鼠", "点击", "点气球", "气球", "捕捉", "catch", "click", "whack", "game")):
        parts.append(_SCENE_GAME)
    # 一个关键词都没匹配时，注入全部（兜底，避免遗漏关键指导）
    if not parts:
        parts = [_SCENE_TOUR, _SCENE_GAME]
    return "\n\n".join(parts)


def build_system_prompt() -> str:
    """静态系统提示词：核心规则 + 素材题材目录。

    这两部分都**不随用户需求变化**，因此所有请求（生成 / 修改）都用同一份，
    放到 system 消息里可最大化命中前缀缓存（DeepSeek 缓存命中的输入价只有未命中的几十分之一）。
    随需求变化的内容（场景包）请走 build_user_prompt。
    """
    parts = [_SYSTEM_PROMPT_CORE]
    try:
        catalog = assets_lib.build_theme_catalog()
    except Exception:
        catalog = ""
    if catalog:
        parts.append(catalog)
    return "\n\n".join(parts)


def _describe_user_image(img) -> str:
    """把一张用户图片描述成「（文件名，宽×高）」这样的可读片段。"""
    if isinstance(img, str):
        return f"（{os.path.basename(img)}）"
    if not isinstance(img, dict):
        return ""
    variant = user_image_variant(img) or {}
    # 优先用原始文件名（GUI 附件里的 name），退化到变体的临时文件名
    name = os.path.basename(str(img.get("name") or variant.get("path") or ""))
    w, h = variant.get("w"), variant.get("h")
    size = f"，{w}×{h}" if w and h else ""
    return f"（{name}{size}）"


def build_user_images_block(user_images, vision: bool = False) -> str:
    """「用户提供的图片」清单（`@user:N`）文案；生成与修改共用同一段。

    vision 表示当前模型能否读图，决定给模型的话术（看不到图时别让它瞎猜画面）。
    """
    if not user_images:
        return ""
    lines = ["【用户提供的图片】用户拖入了以下图片，可直接用作造型/背景："]
    for i, img in enumerate(user_images, 1):
        lines.append(f'  @user:{i} → 造型/背景名请用 "user{i}"{_describe_user_image(img)}')
    lines.append("引用方式：把某个图片文件（.svg/.png）的 content 整行写成 @user:1 这样的占位符，"
                 "程序会自动把用户图片放进工程；代码里用 costume \"user1\" / backdrop \"user1\" 切换。")
    if vision:
        lines.append("你可以看到这些图片，请参考其画面内容决定用途与风格，不要浪费用户提供的图片。")
    else:
        lines.append("你看不到图片内容，请依据用户的文字描述决定图片用途，不要凭空猜测画面。")
    return "\n".join(lines)


def build_user_prompt(user_prompt: str, user_images=None, vision: bool = False) -> str:
    """动态用户消息：场景包（按需求选择）+ 用户图片清单 + 用户需求本身。

    场景包与图片清单都属于「随需求变」的部分，故放在 user 消息里，
    不污染 system 的静态前缀（前缀缓存友好）。
    """
    parts = [_select_scene_pack(user_prompt)]
    block = build_user_images_block(user_images, vision)
    if block:
        parts.append(block)
    parts.append(f"请为我生成实现以下需求的 Scratch 项目文件清单：{user_prompt}")
    return "\n\n".join(parts)


# 分步生成（BUG-03 步骤 4）：复杂需求先出「设计大纲」，再据此生成完整清单。
# P1（BUG-13 / BUG-14）：大纲必须先把「玩家怎么动」「目标从哪来」「每站用哪张背景」定死，
# 否则编码阶段很容易写出「星星在头顶、飞船只能左右」或「彩妆区配派对背景」这类设计错。
OUTLINE_PROMPT = """你是 Scratch 项目设计助手。请**先不要写任何 fractch 代码**，只输出项目的「设计大纲」JSON：

{
  "theme": "一句话说明本项目主题（必须与用户需求一致，不得替换成别的主题）",
  "player": {"sprite": "玩家控制的角色名", "move": "只左右 / 只上下 / 四方向（写清用哪些键）"},
  "collectibles": [{"sprite": "可收集物", "spawn": "从哪出现、怎么移动（写清大致坐标范围）", "reachable": "为什么玩家够得到"}],
  "stations": [{"name": "站点 / 关卡名", "backdrop": "@official:背景名 或 内置模板名 或 空字符串", "content": "这一站讲什么或发生什么"}],
  "sprites": [{"name": "角色名", "asset": "@official:素材名 或 @user:N 或空字符串", "duty": "该角色职责（一句话）"}],
  "backdrops": ["@official:背景素材名", ...],
  "flow": "整体流程概述（2~3 句：怎么开始、怎么推进、怎么结束）"
}

要求：
1. **主题锁定**：先提炼用户需求的核心主题，theme 字段与整个大纲都必须忠实于该主题。
   严禁把主题替换成别的（例如需求是「购物中心游览」，绝不能做成音乐会、体育比赛等无关场景）。
2. 角色 3~6 个，name 只用英文字母/数字/下划线，各不相同。
3. **空间必须自洽 —— 玩家要够得到目标**：
   - `player.move` 写清玩家能沿哪个轴移动（例如「只左右，用左右方向键」）。
   - `collectibles` 的每一项都要写明出现位置/范围，并给出 `reachable` 的理由：
     **玩家只会左右移动时，可收集物的 y 范围必须与玩家所在高度重叠**，不能飘在玩家头顶够不到。
   - 如果写不出「够得到」的理由，就改出现范围（或改玩家的移动方式），不要硬写。
   - 没有可收集物的项目（答题、纯剧情）写空数组 `[]`。
4. **逐站背景必须相关**：
   - 游览 / 多关类项目必须逐站填 `stations`，每站一个 `backdrop`；
     背景必须与该站内容**相关**（美妆区就该用美妆/商场类背景，**不能拿 Party、Refrigerator 这类凑数**）。
   - 素材目录里确实没有相关背景时，**优先从【内置背景模板】里挑**（商店 / 餐厅 / 商场中庭 / 室内 / 城市 等），
     把**模板名**直接写进 `backdrop`（不要加 `@official:` 前缀）；编码阶段会把它展开成完整背景。
   - 模板里也没有合适的，才留空字符串，由编码阶段自绘一张简单背景。
   - **禁止借用不相关的背景**（例如美食区不要用 Party、Refrigerator 凑数）。
   - 单场景项目（没有多站/多关）写空数组 `[]`。
5. **选材**：从文末【素材目录】里选与主题**题材匹配**的角色和背景，用 `@official:<素材名>` 引用；
   素材名必须真实存在于目录，禁止凭记忆编造（不要用目录里没有的名字）。
   同一项目尽量选同一题材、画面协调的素材；目录里确实没有时 asset 留空字符串交给程序兜底。
6. flow 要能支撑一个完整可玩、能收尾的项目；**编码阶段会严格照 `player.move` 与 `stations` 实现**，
   所以这里的设定必须是你真正想要的效果。
只输出 JSON 本体，不要 Markdown、不要解释。"""


def backdrop_template_names() -> str:
    """内置背景模板的名字清单（给大纲阶段看，让它在缺素材时优先选这些）。"""
    return "、".join(name for name, _ in _BACKDROP_TEMPLATES)


def build_backdrop_templates_block(outline: str) -> str:
    """把大纲里用到的内置背景模板展开成 SVG，供编码阶段原样复制。

    只展开「大纲里点到的」模板，避免把 9 张 SVG 全塞进用户消息。
    """
    if not outline:
        return ""
    hits = [(name, svg) for name, svg in _BACKDROP_TEMPLATES if name in outline]
    if not hits:
        return ""
    lines = ["【内置背景模板（把下面的 SVG 原样写成 assets/xxx.svg；"
             "声明与切换都用模板名，不要加 @official: 前缀）】"]
    for name, svg in hits:
        lines.append(f"- {name}：\n  {svg}")
    return "\n".join(lines)


def build_outline_user_prompt(user_prompt: str) -> str:
    """构造「设计大纲」请求的用户消息。"""
    return (f"需求：{user_prompt}\n\n"
            f"【内置背景模板】{backdrop_template_names()}"
            f"（素材库缺对应题材时优先用这些，把模板名直接写进 backdrop）\n\n"
            f"请输出设计大纲。")


REPAIR_PROMPT_FULL = """你上一次的输出有问题，报错如下：

{error}

请修正后重新输出**完整**的「文件清单」JSON，只修改与报错相关的文件，其余没有问题的文件请原样保留，不要改动。
提醒：报错里的文件路径（例如 Guide/main.fractch）对应 files 里 path 为 Guide/main.fractch 的那一项；
files 数组里每项都必须是 {{"path": "...", "content": "..."}} 结构；
如果报错是 "skipped unparsable statement" 或 "unknown block"，说明你用了语法表以外的积木名或写错了语法，请改用语法表中列出的写法；
如果报错是某张背景/某套造型不存在，说明 costume / backdrop 里的名字和 costume 声明的 name 不一致（区分大小写）；
如果提示某个 .svg 不是合法 SVG，请重新给出一段结构简单、标签闭合、带引号的 <svg> 源码。
报错可能有多条，**请一次把所有列出的问题全部改完**（不要只修第一条、也不要只修一部分），否则会再触发一轮修复。
不要输出任何解释文字，也不要带 Markdown 标记，只输出 JSON。"""


REPAIR_PROMPT = """下面是当前项目的文件内容：

{project_files}

校验时发现以下问题：

{error}

请**只输出需要改动的文件**，JSON 格式：
{{"files": [{{"path": "相对路径", "content": "该文件修改后的完整内容"}}, ...]}}
没列出、没改动的文件会保持原样，所以**不要重复输出没有问题的文件**（它们在文件很多时会让输出过长）。
提醒：报错里的文件路径（例如 Guide/main.fractch）对应上面 === 文件名 === 的那一项；
上面已给出每个文件的**完整**内容，改哪个就输出哪个的完整内容（不是片段或 diff）；
path 必须与上面给出的路径完全一致（含 Stage/、assets/ 等前缀）；
如果报错是 "skipped unparsable statement" 或 "unknown block"，说明你用了语法表以外的积木名或写错了语法，请改用语法表中列出的写法；
如果报错是某张背景/某套造型不存在，说明 costume / backdrop 里的名字和 costume 声明的 name 不一致（区分大小写）；
如果提示某个 .svg 不是合法 SVG，请重新给出一段结构简单、标签闭合、带引号的 <svg> 源码。
只做修复上面这些问题所需的**最小改动**，不要把没问题的部分顺手重写、重构或删掉（改动越少，越不容易改坏其他功能）。
报错可能有多条，**请一次把所有列出的问题全部改完**（不要只修第一条、也不要只修一部分），否则会再触发一轮修复。
不要输出任何解释文字，也不要带 Markdown 标记，只输出 JSON。"""


MODIFY_PROMPT = """下面是一个 fractch 项目当前的相关文件内容（为控制开销，只列出了与本次修改相关的文件，可能不含全部文件）：

{project_files}

用户希望做如下修改：
{feedback}

请修改需要改动的文件，输出 JSON：
{{"files": [{{"path": "相对路径", "content": "该文件修改后的完整内容"}}, ...]}}

要求：
1. 只列出需要修改的文件；不需要改的文件不要列（未列出的文件保持原样）。
2. **只能改上面列出的文件**，不要凭空新增文件，也不要改写未列出的文件；
   并且只做实现本次修改所需的**最小改动**，不要把无关部分顺手重写、重构或删掉。
3. 每个 content 是该文件修改后的**完整**内容，不是片段或 diff。
4. path 必须与上面给出的文件路径完全一致（含 Stage/、assets/ 等前缀）。
5. 严格使用 System Prompt 里列出的 fractch 语法，不要发明新名字。
6. content 里 fractch 代码的换行用 \\n、双引号用 \\" 转义。
7. 只输出 JSON 本体，不要 Markdown 代码块，不要任何解释。"""
