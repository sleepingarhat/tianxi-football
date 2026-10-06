/**
 * 足球隊名中文對照（盡量跟香港賽馬會官方譯名）
 * 純展示層對照表：唔碰凍結預測、唔入模、唔改對帳。
 * 鍵用 football-data.co.uk 短名；逐批新賽程必須先補齊展示譯名。
 */

const E0: Record<string, string> = {
  Arsenal: "阿仙奴",
  "Man City": "曼城",
  Leeds: "列斯聯",
  Hull: "侯城",
  Brighton: "白禮頓",
  Chelsea: "車路士",
  Brentford: "賓福特",
  Liverpool: "利物浦",
  Everton: "愛華頓",
  Ipswich: "葉士域治",
  "Nott'm Forest": "諾定咸森林",
  Newcastle: "紐卡素",
  "Man United": "曼聯",
  Sunderland: "新特蘭",
  Bournemouth: "般尼茅夫",
  "Crystal Palace": "水晶宮",
  Tottenham: "熱刺",
  Fulham: "富咸",
  "Aston Villa": "阿士東維拉",
  Coventry: "高雲地利",
};

const D1: Record<string, string> = {
  Freiburg: "弗賴堡",
  Dortmund: "多蒙特",
  Augsburg: "奧格斯堡",
  "Bayern Munich": "拜仁慕尼黑",
  "RB Leipzig": "RB萊比錫",
  Elversberg: "艾華斯堡",
  Leverkusen: "利華古遜",
  Mainz: "緬恩斯",
  "Ein Frankfurt": "法蘭克福",
  "Werder Bremen": "雲達不萊梅",
  "Schalke 04": "史浩克零四",
  "FC Koln": "科隆",
  Hoffenheim: "賀芬咸",
  Stuttgart: "史特加",
  Paderborn: "柏德博恩",
  "Union Berlin": "柏林聯",
  "M'gladbach": "慕遜加柏",
  Hamburg: "漢堡",
};

const SP1: Record<string, string> = {
  Barcelona: "巴塞隆拿",
  "Real Madrid": "皇家馬德里",
  Betis: "貝迪斯",
  Alaves: "艾拉維斯",
  "Ath Madrid": "馬德里體育會",
  Sevilla: "西維爾",
  "La Coruna": "拉科魯尼亞",
  Espanol: "愛斯賓奴",
  "Ath Bilbao": "畢爾包",
  Santander: "桑坦德",
  Osasuna: "奧沙辛拿",
  Sociedad: "皇家蘇斯達",
  Levante: "利雲特",
  Getafe: "基達菲",
  Celta: "切爾達",
  Vallecano: "華歷簡奴",
  Malaga: "馬拉加",
  Villarreal: "維拉利爾",
  Elche: "艾爾切",
  Valencia: "華倫西亞",
};

const I1: Record<string, string> = {
  Roma: "羅馬",
  Inter: "國際米蘭",
  Como: "科木",
  Lazio: "拉素",
  Cagliari: "卡利亞里",
  Milan: "AC米蘭",
  Frosinone: "費辛隆尼",
  Juventus: "祖雲達斯",
  Sassuolo: "莎索羅",
  Napoli: "拿玻里",
  Atalanta: "阿特蘭大",
  Lecce: "萊切",
  Udinese: "烏甸尼斯",
  Torino: "拖連奴",
  Fiorentina: "費倫天拿",
  Bologna: "博洛尼亞",
  Parma: "帕爾馬",
  Monza: "蒙沙",
  Genoa: "熱拿亞",
  Venezia: "威尼斯",
};

const F1: Record<string, string> = {
  Lille: "里爾",
  Monaco: "摩納哥",
  Rennes: "雷恩",
  Lyon: "里昂",
  "Paris FC": "巴黎FC",
  Strasbourg: "斯特拉斯堡",
  Brest: "比斯特",
  "Paris SG": "巴黎聖日耳門",
  Lorient: "羅連安特",
  Lens: "朗斯",
  Angers: "昂熱",
  Troyes: "特魯瓦",
  Marseille: "馬賽",
  "Le Mans": "利文斯",
  Auxerre: "歐塞爾",
  "Le Havre": "勒哈弗爾",
  Toulouse: "圖盧茲",
  Nice: "尼斯",
};

const EC: Record<string, string> = {
  "Aldershot": "艾迪索特",
  "Altrincham": "艾川查姆",
  "Barrow": "巴羅",
  "Boreham Wood": "波利咸活特",
  "Boston Utd": "波士頓聯",
  "Carlisle": "卡素爾",
  "Eastleigh": "伊斯特利",
  "Forest Green": "格連森林",
  "Fylde": "菲爾德",
  "Gateshead": "基斯赫德",
  "Halifax": "夏利法斯",
  "Harrogate": "哈洛格特",
  "Hartlepool": "哈特普爾",
  "Hornchurch": "漢恩徹奇",
  "Kidderminster": "京達米士特",
  "Scunthorpe": "斯肯索普",
  "Solihull": "索利赫爾",
  "Southend": "修安聯",
  "Sutton": "瑟頓聯",
  "Tamworth": "譚禾夫",
  "Wealdstone": "石威德",
  "Woking": "禾京",
  "Worthing": "禾京頓",
  "Yeovil": "伊奧維",
};

const E1: Record<string, string> = {
  Birmingham: "伯明翰", Blackburn: "布力般流浪", Bolton: "保頓", "Bristol City": "布里斯托城",
  Burnley: "般尼", Cardiff: "卡迪夫城", Charlton: "查爾頓", Derby: "打比郡",
  Lincoln: "林肯城", Middlesbrough: "米杜士堡", Millwall: "米禾爾", Norwich: "諾域治",
  Portsmouth: "樸茨茅夫", Preston: "普雷斯頓", QPR: "昆士柏流浪", "Sheffield United": "錫菲聯",
  Southampton: "修咸頓", Stoke: "史篤城", Swansea: "史雲斯", Watford: "屈福特",
  "West Brom": "西布朗", "West Ham": "韋斯咸", Wolves: "狼隊", Wrexham: "域斯咸",
};
const E2: Record<string, string> = {
  "AFC Wimbledon": "AFC溫布頓", Barnsley: "班士利", Blackpool: "黑池", Bradford: "巴拉福特",
  Bromley: "布羅姆利", Burton: "伯頓", Cambridge: "劍橋聯", Doncaster: "唐卡士打",
  Huddersfield: "哈特斯菲爾德", Leicester: "李斯特城", "Leyton Orient": "奧連特", Luton: "盧頓",
  Mansfield: "曼斯菲特", "Milton Keynes Dons": "米爾頓凱恩斯", "Notts County": "諾士郡", Oxford: "牛津聯",
  Peterboro: "彼德堡", Plymouth: "普利茅夫", Reading: "雷丁", "Sheffield Weds": "錫周三",
  Stevenage: "史提芬納治", Stockport: "史托港", Wigan: "韋根", Wycombe: "韋甘比",
};
const E3: Record<string, string> = {
  Accrington: "阿克寧頓", Barnet: "班列特", "Bristol Rvs": "布里斯托流浪", Cheltenham: "車頓咸",
  Chesterfield: "車士打菲特", Colchester: "高車士打", "Crawley Town": "卡維尼", Crewe: "克魯",
  Exeter: "埃克塞特", "Fleetwood Town": "費列活特", Gillingham: "基寧咸", Grimsby: "甘士比",
  "Newport County": "紐波特郡", Northampton: "諾咸頓", Oldham: "奧咸", "Port Vale": "維爾港",
  Rochdale: "羅奇代爾", Rotherham: "洛達咸", Salford: "沙福特城", Shrewsbury: "梳士貝利",
  Swindon: "史雲頓", Tranmere: "燦美爾", Walsall: "華素爾", York: "約克城",
};
const SP2: Record<string, string> = {
  Albacete: "阿爾巴塞特", Almeria: "艾美利亞", Andorra: "安道爾FC", Burgos: "布爾戈斯",
  Cadiz: "卡迪斯", Castellon: "卡斯迪隆", "Celta B": "切爾達B隊", Ceuta: "休達",
  Cordoba: "科爾多瓦", Eibar: "伊巴", Eldense: "艾爾丹斯", Girona: "基羅納",
  Granada: "格蘭納達", "Las Palmas": "拉斯彭馬斯", Leganes: "雷加利斯", Mallorca: "馬略卡",
  Oviedo: "奧維多", Sabadell: "沙巴度爾", "Sociedad B": "皇家蘇斯達B隊", "Sp Gijon": "希杭",
  Tenerife: "特內里費", Valladolid: "華拉度列",
};
const D2: Record<string, string> = {
  Bielefeld: "比勒費爾德", Bochum: "波琴", Braunschweig: "布倫瑞克", Cottbus: "科特布斯",
  Darmstadt: "達斯泰特", Dresden: "特雷斯登", "Greuther Furth": "格雷特霍夫", Hannover: "漢諾威",
  Heidenheim: "海登咸", Hertha: "哈化柏林", "Holstein Kiel": "基爾", Kaiserslautern: "凱沙羅頓",
  Karlsruhe: "卡爾斯魯厄", Magdeburg: "馬格德堡", Nurnberg: "紐倫堡", Osnabruck: "奧斯納布呂克",
  "St Pauli": "聖保利", Wolfsburg: "禾夫斯堡",
};
const I2: Record<string, string> = {
  Arezzo: "阿雷素", Ascoli: "阿斯科利", Avellino: "阿維利諾", Benevento: "賓尼雲圖",
  Carrarese: "卡拉利斯", Catanzaro: "卡坦沙羅", Cesena: "切辛納", Cremonese: "克雷莫納",
  Empoli: "安玻里", "Juve Stabia": "祖雲斯塔比亞", Mantova: "曼托瓦", Modena: "摩德納",
  Padova: "帕多瓦", Palermo: "巴勒莫", Pisa: "比薩", Sampdoria: "森多利亞",
  Sudtirol: "南蒂羅爾", Verona: "維羅納", Vicenza: "維琴察", "Virtus Entella": "維圖斯恩特拉",
};
const F2: Record<string, string> = {
  Annecy: "安納西", Boulogne: "布羅尼", Clermont: "克萊蒙", Dijon: "迪安",
  Dunkerque: "登卡基", Grenoble: "格勒諾布爾", Guingamp: "甘岡", Laval: "拉瓦勒",
  Metz: "梅斯", Montpellier: "蒙彼利埃", Nancy: "南錫", Nantes: "南特",
  "Pau FC": "波城", "Red Star": "紅星", Reims: "蘭斯", Rodez: "羅德茲",
  Sochaux: "索察", "St Etienne": "聖伊天",
};

const BY_DIV: Record<string, Record<string, string>> = { E0, E1, E2, E3, EC, D1, D2, SP1, SP2, I1, I2, F1, F2 };

/** 跨聯賽後備：短名全站唯一先用得，撞名就唔亂譯 */
const NORM = new Map<string, string>();
const GLOBAL = (() => {
  const seen = new Map<string, string | null>();
  for (const table of Object.values(BY_DIV)) {
    for (const [en, zh] of Object.entries(table)) {
      seen.set(en, seen.has(en) && seen.get(en) !== zh ? null : zh);
      const n = en.toLowerCase().replace(/[^a-z0-9]/g, "");
      NORM.set(n, zh);
    }
  }
  return seen;
})();

/** 展示用中文隊名；原文只保留作內部 join key。 */
export function teamZh(div: string, name: string): string {
  const res = BY_DIV[div]?.[name] ?? GLOBAL.get(name);
  if (res) return res;
  const n = name.toLowerCase().replace(/[^a-z0-9]/g, "");
  const stripped = n.replace(/(?:associationfootballclub|footballclub|united|utd|afc|fc|cf|sc)$/g, "");
  return NORM.get(n) ?? NORM.get(stripped) ?? name;
}
