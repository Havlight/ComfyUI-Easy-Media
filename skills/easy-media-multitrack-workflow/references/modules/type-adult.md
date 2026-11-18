适用：成人、情色、NSFW 或亲密肢体互动的多段影片与分镜规划

# 成人与亲密互动分镜

在 [craft.md](../craft.md) 镜头分工（胸上表情反应近景 ↔ 脸部出画的局部接触特写）的基础上，补充成人题材通用的防崩坏工程底线、直白词汇与资产规范。

## 预设方案路由（优先检查）

规划前先检查用户是否指定预设代号，或按默认方案载入（预设文件内有不同规定时，**以预设文件覆盖本文件与 `craft.md` 默认**；提示词模型格式照 [writing.md](../writing.md)「用哪份指南」对应分页模型）：

- **预设 A（默认生效）**——[optional/A-adult-director-preset.md](../optional/A-adult-director-preset.md)：摄影基调锁定、景别与贴身第三人称／主观 POV 调度、直白交合特写与全体位动作编排。用户说「照预设 A／AV 预设 A」，或未指定其他成人预设时默认选用（在分镜方案开头注明「套用预设 A」；若日后 `optional/` 下新增 `B-...`、`C-...` 等其他成人预设且用户未指明，先列出一句话让用户选）。

## 分镜、遮挡与人数防崩坏底线

- **跳过进入与生理变化过程**：脱衣、掏取、生理状态变化与远距离对准的瞬间极易变形融化，一律在切镜（`shot`）中省略，直接从已经建立稳定接触（或紧贴入口预备推进）、支撑面固定的姿势开拍，并写明身体压在哪个家具或平面上（床垫、沙发、桌面、地板）。
- **遮挡剔除与正向避脸**：背面姿态不回头时不写正面胸部与五官；脸部出画的局部特写删掉所有表情与视线描述。凡未提供五官设定的角色（如遮头脸男角躯干板），一律用**正向光学透视**（POV、过肩前景、腰胯／跨下视角）自然将头脸留在画框外，**严禁写 `headless`、`no face`、`head cropped out` 等负面裁切词**（防模型抓到头脸名词反而画出假脸）。
- **主观视角防悬空**：第一人称 POV 连同视野下缘／侧边可见的自身下腹、大腿根部或扶握双手一起写出，不让局部器官从画面底边单独悬空探出。
- **多人分工与防镜像**：双人以上服务或接触同一对象时，写明画面总人数、共用单一目标主体与各自左右不同部位的具体动作，不笼统写同时做同一动作（防目标部位重复生成）；三人以上交缠或对称俯视构图时，以核心角色为主词在同一句集中写清双手与身体分工，并正向锚定空侧物件（如 `empty grey pillow on her left side`）防镜像多生人物。

## 表演与发声铁律

- **写实表情防崩**：避免狂乱、斗鸡眼（`cross-eyed`）等令写实五官崩坏的词，改用具体微表情（咬下唇、半阖迷离眼、眼尾泛泪、失焦微张嘴拉丝）。
- **口部占用禁对嘴台词**：接吻或口部被含覆占用期间**严禁放对嘴 `<d>` 台词**（防强行张嘴穿模或多长嘴）；呼吸、鼻息与接触水声一律写在结尾的环境与物理音效段（若需心声独白，须标明闭嘴画外音 `says in an off-screen voiceover: <d>…</d> while her lips remain…`）。

## 直白精确英文词汇表（禁委婉词，各模型共用）

写实成人视频模型（含 10Eros Beta 7、Ultima 系列等）由**直白通俗触发词**与**解剖／光学写实描述**共同驱动。使用委婉词会让模型丢失接触坐标，导致器官融化、悬空磨蹭或动作僵死：

- **禁用委婉词与否定词黑名单**：严禁写 `intimate act`、`anatomical coupling`、`lovemaking`、`pleasuring him/her`、`oral attention`、`oral service`、`member`、`manhood`、`lower region`、`private parts` 及 `without being overly explicit` 等自我审查句；同时严禁写 `no face`、`not white`、`no milk` 等否定词（H3 无负面提示词框，写否定名词会反向画出该特征）。
- **六大类正向精确词汇速查（按镜头需要选用，不写底线标签）**：
  1. **男性性器（防无特征肉柱／香肠化）**
     - 核心直白词：`erect penis`、`thick erect cock`、`slick shaft`、`heavy testicles / balls`
     - 解剖结构锚点：`distinct mushroom-shaped glans and coronal ridge`（伞状龟头与冠状沟边缘——**仅用于口手奉仕或外射等龟头外露镜；已插入抽插时龟头在体内不可见，禁写龟头外观，改写阴唇紧包进入的轴身根部与骨盆贴撞**）、`subtle surface veins along the shaft`、`taut glistening foreskin / shaft skin`
  2. **女性私处、乳房与后庭（防肉块糊成一片）**
     - 核心直白词：`wet juicy pussy`、`glistening pussy-lips`、`exposed clitoris`、`puckered anus`、`soft natural breasts`、`erect nipples`
     - 解剖与软组织受力：`parted inner labia glistening with clear arousal fluid`、`plump outer labia`、`teardrop breast sag with natural gravity sway`、`soft thigh and breast flesh compressing at the contact point`
  3. **口交、手交、足交与乳交微动作（锁死唇手接触几何）**
     - 口部奉仕：`sucking his erect penis rhythmically`、`stretched glossy lips wrapped tightly around the wet shaft`、`hollowed cheeks`、`flattened tongue licking along the underside vein from base to glans`、`taking the shaft deep with a subtle throat bulge`
     - 手／足／胸奉仕：`fingers wrapped firmly around the base stroking up and down`、`pressing her soft bare breasts tightly together around the slick shaft (titjob)`、`arched soles in sheer stockings gripping and stroking the erect shaft`
  4. **插入、抽送耦合与肉体受力反应（防悬空错位）**
     - 耦合与抽送：`erect cock sliding / thrusting steadily into her wet stretched pussy`、`glistening pussy-lips clinging tightly around the entering shaft`、`hips driving forward in rhythmic deep thrusts`、`riding up and down on his erect shaft`
     - 物理受力反馈：`soft flesh ripple across her buttock cheeks on each hip impact`、`lower stomach tensing`、`toes curling against the sheets`
  5. **体液量级与光学质感（防白漆面具与闭眼白内障）**
     - 湿润与拉丝（微量／中量）：`thin glistening sheen of clear arousal fluid`、`fine translucent strands of saliva stretching between her lips and the wet glans`
     - 精液质感（防死白油漆块）：`thick pearlescent translucent semen with raw egg-white consistency`、`warm sticky cum spurting across her tongue / lower cheek / breasts and pooling naturally in skin creases`（颜射若闭眼，只写流过鼻梁、脸颊或嘴角，**禁写覆盖闭合眼皮**以防画成睁眼白内障）
     - 潮吹／喷水（防牛奶化）：`pressurized stream of water-clear transparent squirt liquid spurting outward from her wet pussy and soaking the dark sheets`
  6. **同步物理实音（供结尾声音段选用）**
     - `rhythmic wet sucking and slick saliva sounds`、`wet skin-on-skin slap of hips meeting`、`slick wet squelching sounds`、`soft breathy moans and sharp gasps`、`subtle bedsheet rustle`

## 多阶设定板、3D 灰模、槽位隔离与长片巡检

- **角色设定板与三阶段退衣**：
  - *露脸女角板*（排最前顺位 `<Picture 1>`、`<Picture 2>`）：`高解析正脸特写（左）＋ 遮脸正面全身 ＋ 背面全身` 横向拼板。穿搭剥离按实测最稳的三阶段切换：**① 全装 → ② 仅露胸（保留下着裙摆与丝袜高跟）→ ③ 仅丝袜／裤袜＋高跟鞋**（原造型有丝袜高跟时默认保留，床戏亦可直接踩床维持足部一致）。
  - *男角无五官躯干板（未设固定男脸时）*：`遮头脸正面全身 ＋ 背面全身`。正戏裸体阶段的男角板须保持**勃起预备状态**（防生理状态冲突）；多男同场若无刻意体型／肤色反差需求，可共用同一张男角板节省槽位。
- **槽位精简与 `_common_genitals` 严格隔离**：当段未入镜的角色立即撤除其全身设定板（防强塞多余人体）；单人仰躺张腿的私处参考图**仅挂在真正的局部大特写或单女无遮挡张腿展示镜**，凡挂了双人／三人交合灰模的中近景一律**禁混挂**（防将单人阴部错贴到男方大腿造成双骨盆）。
- **两阶段无五官 3D 灰模姿势参考（`Krea2` → `mmh3-edit` Mannequin）**：多人交叠、特殊家具高低差或需精确引导推进向量时，启用 `comfyui-agent-workbench` 制作灰模放参考末位 `<Picture K>`（简单对话或常规单人镜不必另做）：
  1. `krea2-t2i-kroma` 照 `models/krea2/guide.md` 生成苗条紧致体态（细腰与胸型拆写防肥胖遮挡）、正向透视角度、半进入动态或抵住入口且下半截勃起轴身清楚外露的构图底稿；
  2. `mmh3-edit` 照 `models/mmh3/guide.md` 转为纯灰泥雕塑人偶与简易几何块，剥离五官、发色、肤色与房间材质污染；
  3. H3 提示词指明 `<Picture K>` 仅借用机位角度、多人姿态与耦合几何，忽略灰色雕塑材质与几何块。
- **接续与两阶段交付巡检**：同一体位与场景连续推进时**优先善用 `context_anchor`（起始 `shot` 后常态接 `1–3 段`，最多不超过 `5 段`）**，前提是本段开头（`0.00s`）承接上一段末尾的**环境、体位支撑、人物相对位置与机位构图**（进段后可平滑运镜或用 `[Shot 2]` 段内切镜；段内走位或构图大改且后续仍要接续时标 `"anchor": "restart"`）。遇到更换体位或主要位置、切换服装阶段板、换场景、从段首直接硬切新角度／独立特写，或已接满 5 段时，用 `shot` 切开。长片先跑一采 `single` 用 `review_frames.py` 加密抽帧排查多长手脚、无参考角色乱生假脸或穿模（局部问题微调构图词／撤冲突板或换 seed 重抽，最多 3 轮），全数通过后再跑 `upscale` 二采放大并合成存档 MP4。
- **外部词库与参考音声**：若环境装有 `adult-kb` 可按需查阅体位支撑几何与微动作并全部转译为纯英文自然语言（禁写底线标签或编号）；若本机存在 `G:/我的雲端硬碟/blender/asset/smutbase/sounds/ref-voice/README.md`，挂参考音声前先读该引导档挑档。
