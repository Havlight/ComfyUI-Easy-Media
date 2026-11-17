适用：成人、情色、NSFW 或亲密肢体互动的多段影片与分镜规划

# 成人与亲密互动分镜

在 [craft.md](../craft.md) 镜头分工（胸上表情反应近景 ↔ 脸部出画的局部接触特写）的基础上，补充亲密题材的通用底层规则。

## 预设方案路由（优先检查）

规划前先检查用户是否指定预设代号，或按默认方案载入（预设文件内有不同规定时，**以预设文件覆盖本文件与 `craft.md` 默认**）：

- **预设 A（默认生效）**——[optional/A-pov-immersive-longform.md](../optional/A-pov-immersive-longform.md)：主观沉浸 POV 主导与动态视线、正向光学透视近景（男角不露脸）、三阶设定板、`Krea2 → mmh3` 3D 灰模姿势参考与两阶段长片巡检。用户说「照预设 A／AV 预设 A」，或未指定其他成人预设／特殊旁观视角时默认选用（在分镜方案开头注明「套用预设 A」；若日后 `optional/` 下新增 `B-...`、`C-...` 等其他成人预设且用户未指明，先列出一句话让用户选）。
- **10Eros Beta 7 模型格式联动**：若目标工作流分页使用 `10Eros`／`Eros Beta 7` 系列模型，一并读取 [optional/eros-beta7.md](../optional/eros-beta7.md) 采用三段式自然语言提示词。
- **Ultima 系列模型格式联动**：若目标工作流分页使用 `Ultima`（含 `Ultima NSFW Hybrid`）系列模型，或用户指名采用 Ultima 写法时，一并读取 [optional/ultima-hybrid.md](../optional/ultima-hybrid.md) 采用 `<Subject N>` 绑定＋骨骼关节力学锚点＋尾端百分比物理滑杆格式。

## 分镜与构图

- 通用基础排法（未套用特定预设时）：交代人物关系与互动开场 → 切入已建立稳定接触的局部特写（脸部出画）→ 切胸上近景拍克制的表情反应与台词 → 换角度近景或平静余韵收尾。
- 跳过进入与生理变化过程：脱衣、掏取、生理状态变化与远距离对准的瞬间极易变形融化，一律在切镜（`shot`）中省略，直接从已经建立稳定接触（或紧贴入口预备推进）、支撑面固定的姿势开拍，并写明身体压在哪个家具或平面上（床垫、沙发、桌面）。
- 遮挡剔除：背面姿态不回头时不写正面胸部与五官；脸部出画的局部特写（如腰臀与大腿近景）删掉所有表情与视线描述，集中在肢体张力、支撑点与接触细节。
- 第一人称主观视角（POV）连同可见的下腹、大腿根部或扶握的手一起写出，不让局部从画面底边单独悬空探出。

## 多人与动作

- 双人以上服务或接触同一对象时，写明画面总人数、共用单一目标主体，并分配左右不同部位的具体动作（如左者扶握根部、右者贴近亲吻辅助），不笼统写两人同时做同一动作（易导致目标部位重复生成或镜像多出人物）。
- 三人以上交缠或双人近距离协作时，先以核心角色为主词在同一句写清双手与身体各处的同步分工稳住肢体；手边已有符合角色与场景设定、结构无误的起始图（且该段不需绑定 `<Audio N>` 音色）时，也可用 `default` 作首帧驱动稳定节奏动作。

## 表演与声音

- 避免狂乱、极度失神等夸张词汇（H3 易把五官画崩），改用具体克制的面部细节（如浅笑、咬下唇、垂眸再抬眼、轻喘）。
- 接吻或口部被占用期间不放 `<d>` 台词或说话人声（否则模型强行张嘴发话会导致穿模或多长部位），呼吸、鼻息与接触声一律写在结尾的环境与物理音效段。

## 直白精确英文词汇表（禁委婉词，各模型共用）

写实成人视频模型（含 10Eros Beta 7、Ultima 系列等）的训练集由**直白通俗触发词**与**解剖／光学写实描述**共同驱动。使用文学化、医学化委婉词会让模型丢失接触坐标，导致器官融化、悬空磨蹭或动作僵死：

- **禁用委婉词与套话黑名单**：严禁写 `intimate act`、`anatomical coupling`、`lovemaking`、`pleasuring him/her`、`oral attention`、`oral service`、`member`、`manhood`、`lower region`、`private parts`，严禁残留 `without being overly explicit` 等自我审查句；同时严禁写 `no face`、`not white`、`no milk` 等否定词（H3 无负面提示词框，写否定名词会反向画出该特征，一律只写正向可见形态）。
- **六大类正向精确词汇速查（按镜头需要选用，不写底线标签）**：
  1. **男性性器（防无特征肉柱／香肠化）**
     - 核心直白词：`erect penis`、`thick erect cock`、`slick shaft`、`heavy testicles / balls`
     - 解剖结构锚点：`distinct mushroom-shaped glans and coronal ridge`（伞状龟头与冠状沟边缘）、`subtle surface veins along the shaft`、`taut glistening foreskin / shaft skin`
  2. **女性私处、乳房与后庭（防肉块糊成一片）**
     - 核心直白词：`wet juicy pussy`、`glistening pussy-lips`、`exposed clitoris`、`puckered anus`、`soft natural breasts`、`erect nipples`
     - 解剖与软组织受力：`parted inner labia glistening with clear arousal fluid`、`plump outer labia`、`teardrop breast sag with natural gravity sway`、`soft thigh and breast flesh compressing at the contact point`
  3. **口交、手交、足交与乳交微动作（锁死唇手接触几何）**
     - 口部奉仕：`sucking his erect penis rhythmically`、`stretched glossy lips wrapped tightly around the wet shaft`、`hollowed cheeks`、`flattened tongue licking along the underside vein from base to glans`、`taking the shaft deep with a subtle throat bulge`
     - 手／足／胸奉仕：`fingers wrapped firmly around the base stroking up and down`、`pressing her soft oiled breasts tightly together around the slick shaft (titjob)`、`arched soles in sheer stockings gripping and stroking the erect shaft`
  4. **插入、抽送耦合与肉体受力反应（防悬空错位）**
     - 耦合与抽送：`erect cock sliding / thrusting steadily into her wet stretched pussy`、`glistening pussy-lips clinging tightly around the entering shaft`、`hips driving forward in rhythmic deep thrusts`、`riding up and down on his erect shaft`
     - 物理受力反馈：`soft flesh ripple across her buttock cheeks on each hip impact`、`lower stomach tensing`、`toes curling against the sheets`
  5. **体液量级与光学质感（防白漆面具与闭眼白内障）**
     - 湿润与拉丝（微量／中量）：`thin glistening sheen of clear arousal fluid`、`fine translucent strands of saliva stretching between her lips and the wet glans`
     - 精液质感（防死白油漆块）：`thick pearlescent translucent semen with raw egg-white consistency`、`warm sticky cum spurting across her tongue / lower cheek / breasts and pooling naturally in skin creases`（颜射若闭眼，只写流过鼻梁、脸颊或嘴角，**禁写覆盖闭合眼皮**以防画成睁眼白内障）
     - 潮吹／喷水（防牛奶化）：`pressurized stream of water-clear transparent squirt liquid spurting outward from her wet pussy and soaking the dark sheets`
  6. **同步物理实音（供结尾声音段选用）**
     - `rhythmic wet sucking and slick saliva sounds`、`wet skin-on-skin slap of hips meeting`、`slick wet squelching sounds`、`soft breathy moans and sharp gasps`、`subtle bedsheet rustle`

## 参考与词库

- 穿搭经历多阶段剥离时，按实测最稳的三阶段准备角色设定图并在分段时切换（比单靠文字改服装稳得多）：**① 全装 → ② 仅露胸（保留裙摆／下着与丝袜高跟）→ ③ 仅丝袜／裤袜＋高跟鞋**（原造型有丝袜或高跟鞋时默认保留，床戏亦可直接踩床）。
- 若环境装有 `adult-kb` 技能可按需查阅体位支撑几何与微动作，须全部转译为纯英文自然语言，不把底线标签或词库编号写进提示词。
- 若本机存在 `G:/我的雲端硬碟/blender/asset/smutbase/sounds/ref-voice/README.md`，需要挂参考音声（呻吟、喘息、口交、亲吻）时先读该引导档挑档。
