// Sanscript 1.3.3 (github.com/indic-transliteration/sanscript.js @24e15105, MIT) with the common_maps schemes it needs
// (github.com/indic-transliteration/common_maps @c6fc8ca4, MIT). Built for Family Tree keeping only the Telugu,
// Devanagari and a few roman schemes; the library code below is unmodified. See LICENSE.
const schemes = {};
schemes.telugu = {"vowels": {"अ": "అ", "आ": "ఆ", "इ": "ఇ", "ई": "ఈ", "उ": "ఉ", "ऊ": "ఊ", "ऋ": "ఋ", "ॠ": "ౠ", "ऌ": "ఌ", "ॡ": "ౡ", "ऎ": "ఎ", "ए": "ఏ", "ऐ": "ఐ", "ऒ": "ఒ", "ओ": "ఓ", "औ": "ఔ"}, "vowel_marks": {"ा": "ా", "ि": "ి", "ी": "ీ", "ु": "ు", "ू": "ూ", "ृ": "ృ", "ॄ": "ౄ", "ॢ": "ౢ", "ॣ": "ౣ", "ॆ": "ె", "े": "ే", "ै": "ై", "ॊ": "ొ", "ो": "ో", "ौ": "ౌ"}, "yogavaahas": {"ं": "ం", "ः": "ః", "ँ": "ఁ"}, "virama": {"्": "్"}, "consonants": {"क": "క", "ख": "ఖ", "ग": "గ", "घ": "ఘ", "ङ": "ఙ", "च": "చ", "छ": "ఛ", "ज": "జ", "झ": "ఝ", "ञ": "ఞ", "ट": "ట", "ठ": "ఠ", "ड": "డ", "ढ": "ఢ", "ण": "ణ", "त": "త", "थ": "థ", "द": "ద", "ध": "ధ", "न": "న", "प": "ప", "फ": "ఫ", "ब": "బ", "भ": "భ", "म": "మ", "य": "య", "र": "ర", "ल": "ల", "व": "వ", "श": "శ", "ष": "ష", "स": "స", "ह": "హ", "ळ": "ళ", "क्ष": "క్ష", "ज्ञ": "జ్ఞ"}, "symbols": {"०": "౦", "१": "౧", "२": "౨", "३": "౩", "४": "౪", "५": "౫", "६": "౬", "७": "౭", "८": "౮", "९": "౯", "ॐ": "ఓం", "ऽ": "ఽ", "।": "।", "॥": "॥"}, "extra_consonants": {"क़": "", "ख़": "", "ग़": "", "ज़": "", "ड़": "", "ढ़": "", "फ़": "", "य़": "", "ऱ": "ఱ", "ऴ": "ఴ", "ऩ": ""}};
schemes.devanagari = {"vowels": {"अ": "अ", "आ": "आ", "इ": "इ", "ई": "ई", "उ": "उ", "ऊ": "ऊ", "ऋ": "ऋ", "ॠ": "ॠ", "ऌ": "ऌ", "ॡ": "ॡ", "ऎ": "ऎ", "ए": "ए", "ऐ": "ऐ", "ऒ": "ऒ", "ओ": "ओ", "औ": "औ", "ऍ": "ऍ", "ऑ": "ऑ"}, "vowel_marks": {"ा": "ा", "ि": "ि", "ी": "ी", "ु": "ु", "ू": "ू", "ृ": "ृ", "ॄ": "ॄ", "ॢ": "ॢ", "ॣ": "ॣ", "ॆ": "ॆ", "े": "े", "ै": "ै", "ॊ": "ॊ", "ो": "ो", "ौ": "ौ", "ॅ": "ॅ", "ॉ": "ॉ"}, "yogavaahas": {"ं": "ं", "ः": "ः", "ँ": "ँ", "ᳵ": "ᳵ", "ᳶ": "ᳶ", "ꣳ": "ꣳ"}, "virama": {"्": "्"}, "consonants": {"क": "क", "ख": "ख", "ग": "ग", "घ": "घ", "ङ": "ङ", "च": "च", "छ": "छ", "ज": "ज", "झ": "झ", "ञ": "ञ", "ट": "ट", "ठ": "ठ", "ड": "ड", "ढ": "ढ", "ण": "ण", "त": "त", "थ": "थ", "द": "द", "ध": "ध", "न": "न", "प": "प", "फ": "फ", "ब": "ब", "भ": "भ", "म": "म", "य": "य", "र": "र", "ल": "ल", "व": "व", "श": "श", "ष": "ष", "स": "स", "ह": "ह", "ळ": "ळ", "क्ष": "क्ष", "ज्ञ": "ज्ञ"}, "symbols": {"०": "०", "१": "१", "२": "२", "३": "३", "४": "४", "५": "५", "६": "६", "७": "७", "८": "८", "९": "९", "ॐ": "ॐ", "ऽ": "ऽ", "।": "।", "॥": "॥"}, "zwj": {"‍": "‍"}, "zwnj": {"‌": "‌"}, "skip": {"": ""}, "accents": {"॑": "॑", "᳚": "᳚", "॒": "॒", "᳒": "᳒", "᳙": "᳙", "᳓": "᳓", "꣡": "꣡", "꣢": "꣢", "꣣": "꣣", "꣤": "꣤", "꣥": "꣥", "꣦": "꣦", "꣧": "꣧", "꣨": "꣨", "꣩": "꣩", "꣪": "꣪", "꣫": "꣫", "꣬": "꣬", "꣭": "꣭", "꣮": "꣮", "꣯": "꣯", "꣰": "꣰", "꣱": "꣱", "᳝": "᳝", "᳸": "᳸"}, "candra": {"ॅ": "ॅ"}, "extra_consonants": {"क़": "क़", "ख़": "ख़", "ग़": "ग़", "ज़": "ज़", "ड़": "ड़", "ढ़": "ढ़", "फ़": "फ़", "य़": "य़", "ऱ": "ऱ", "ऴ": "ऴ", "ऩ": "ऩ"}, "alternates": {"᳙": ["᳡"], "क़": ["क़"], "ख़": ["ख़"], "फ़": ["फ़"], "ज़": ["ज़"], "ऩ": ["ऩ"], "ड़": ["ड़"], "ग़": ["ग़"], "ढ़": ["ढ़"], "य़": ["य़"], "ऱ": ["ऱ"], "ऴ": ["ऴ"]}};
schemes.iso = {"vowels": {"अ": "a", "आ": "ā", "इ": "i", "ई": "ī", "उ": "u", "ऊ": "ū", "ऋ": "r̥", "ॠ": "r̥̄", "ऌ": "l̥", "ॡ": "l̥̄", "ऎ": "e", "ए": "ē", "ऐ": "ai", "ऒ": "o", "ओ": "ō", "औ": "au", "ऍ": "ê", "ऑ": "ô"}, "yogavaahas": {"ं": "ṁ", "ः": "ḥ", "ᳵ": "ẖ", "ḫ": "ᳶ", "ँ": "m̐"}, "virama": {"्": ""}, "zwj": {"‍": "{}"}, "accents": {"॑": "̭", "॒": "̱", "᳙": "̀", "᳓": "́", "꣡": "¹", "꣢": "²", "꣣": "³", "꣤": "⁴", "꣥": "⁵", "꣦": "⁶", "꣧": "⁷", "꣨": "⁸", "꣩": "⁹", "꣪": "ᵃ", "꣫": "ᵘ", "꣬": "ᵏ", "꣭": "ⁿ", "꣮": "ᵖ", "꣯": "ʳ", "꣰": "ᵛ", "꣱": "ˢ"}, "consonants": {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ṅ", "च": "c", "छ": "ch", "ज": "j", "झ": "jh", "ञ": "ñ", "ट": "ṭ", "ठ": "ṭh", "ड": "ḍ", "ढ": "ḍh", "ण": "ṇ", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "ś", "ष": "ṣ", "स": "s", "ह": "h", "ळ": "ḷ", "क्ष": "kṣ", "ज्ञ": "jñ"}, "symbols": {"०": "0", "१": "1", "२": "2", "३": "3", "४": "4", "५": "5", "६": "6", "७": "7", "८": "8", "९": "9", "ॐ": "ōṁ", "ऽ": "'", "।": "।", "॥": "॥"}, "extra_consonants": {"क़": "q", "ख़": "k͟h", "ग़": "ġ", "ज़": "z", "ड़": "ṛ", "ढ़": "ṛh", "फ़": "f", "य़": "ẏ", "ऱ": "ṟ", "ऴ": "ḻ", "ऩ": "ṉ", "ऱ्": "r̆", "थ़": "s̱", "स़": "s̤", "ह़": "h̤", "त़": "t̤", "झ़": "ž", "च़": "ʦ", "छ़": "ʦh", "ذ": "ẕ", "ض": "ż", "ظ": "ẓ", "व़": "w"}, "accented_vowel_alternates": {"á": ["á"], "é": ["é"], "í": ["í"], "aí": ["aí"], "ó": ["ó"], "ú": ["ú"], "aú": ["aú"], "ŕ̥": ["ŕ̥", "ŕ̥"], "ŕ̥̄": ["ŕ̥̄", "ŕ̥̄", "ŕ̥̄", "r̥̄́", "r̥̄́", "r̥̄́", "ŕ̥̄"], "à": ["à"], "è": ["è"], "ì": ["ì"], "aì": ["aì"], "ò": ["ò"], "ù": ["ù"], "aù": ["aù"], "r̥̀": ["r̥̀"], "r̥̀̄": ["r̥̀̄", "r̥̄̀", "r̥̄̀", "r̥̄̀", "r̥̀̄"]}, "alternates": {"|": [".", "/"], "||": ["..", "//"], "'": ["`"], "m̐": ["ṁ"], "ṝ": ["ṝ", "r̥̄", "r̥̄", "ṝ", "ṝ"], "ṃ": ["ṃ"], "ḥ": ["ḥ"], "ṭ": ["ṭ"], "ṭh": ["ṭh"], "ḍ": ["ḍ"], "ḍh": ["ḍh"], "ṇ": ["ṇ"], "ṅ": ["ṅ"], "ñ": ["ñ"], "ṣ": ["ṣ"], "ṛ": ["ṛ"], "ṛh": ["ṛh"], "k͟h": ["ḵẖ", "ḵh"], "q": ["ḳ"], "w": ["ẉ"], "ġ": ["g̠ẖ"], "́": ["¹"], "r̥": ["r̤i"]}, "isRomanScheme": true};
schemes.kolkata_v2 = {"vowels": {"अ": "a", "आ": "ā", "इ": "i", "ई": "ī", "उ": "u", "ऊ": "ū", "ऋ": "ṛ", "ॠ": "ṝ", "ऌ": "ḷ", "ॡ": "ḹ", "ऎ": "e", "ए": "ē", "ऐ": "ai", "ऒ": "o", "ओ": "ō", "औ": "au"}, "yogavaahas": {"ं": "ṃ", "ः": "ḥ", "ँ": "~"}, "virama": {"्": ""}, "accents": {"॑": "̭", "॒": "̱", "᳙": "̀", "᳓": "́", "꣡": "¹", "꣢": "²", "꣣": "³", "꣤": "⁴", "꣥": "⁵", "꣦": "⁶", "꣧": "⁷", "꣨": "⁸", "꣩": "⁹", "꣪": "꣪", "꣫": "꣫", "꣬": "꣬", "꣭": "꣭", "꣮": "꣮", "꣯": "꣯", "꣰": "꣰", "꣱": "꣱"}, "consonants": {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ṅ", "च": "c", "छ": "ch", "ज": "j", "झ": "jh", "ञ": "ñ", "ट": "ṭ", "ठ": "ṭh", "ड": "ḍ", "ढ": "ḍh", "ण": "ṇ", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "ś", "ष": "ṣ", "स": "s", "ह": "h", "ळ": "ḻ", "क्ष": "kṣ", "ज्ञ": "jñ"}, "symbols": {"०": "0", "१": "1", "२": "2", "३": "3", "४": "4", "५": "5", "६": "6", "७": "7", "८": "8", "९": "9", "ॐ": "ōṃ", "ऽ": "'", "।": "|", "॥": "||"}, "extra_consonants": {"क़": "q", "ख़": "k͟h", "ग़": "ġ", "ज़": "z", "ड़": "r̤", "ढ़": "r̤h", "फ़": "f", "य़": "ẏ", "ऱ": "ṟ", "ऴ": "l̤", "ऩ": "ṉ"}, "alternates": {"|": [".", "/"], "||": ["..", "//"], "'": ["`"], "m̐": ["ṁ"], "ṃ": ["ṃ"], "ḥ": ["ḥ"], "ṭ": ["ṭ"], "ṭh": ["ṭh"], "ḍ": ["ḍ"], "ḍh": ["ḍh"], "ṇ": ["ṇ"], "ṅ": ["ṅ"], "ñ": ["ñ"], "ṣ": ["ṣ"], "ṛ": ["r̥", "ṛ"], "ṝ": ["ṝ", "r̥̄", "r̥̄", "ṝ", "ṝ"], "́": ["¹"]}, "accented_vowel_alternates": {"á": ["á"], "é": ["é"], "í": ["í"], "aí": ["aí"], "ó": ["ó"], "ú": ["ú"], "aú": ["aú"], "à": ["à"], "è": ["è"], "ì": ["ì"], "aì": ["aì"], "ò": ["ò"], "ù": ["ù"], "aù": ["aù"], "ṛ̀": ["r̥̀", "ṛ̀"]}, "isRomanScheme": true};
schemes.itrans = {"vowels": {"अ": "a", "आ": "A", "इ": "i", "ई": "I", "उ": "u", "ऊ": "U", "ऋ": "RRi", "ॠ": "RRI", "ऌ": "LLi", "ॡ": "LLI", "ऎ": "è", "ए": "e", "ऐ": "ai", "ऒ": "ò", "ओ": "o", "औ": "au"}, "yogavaahas": {"ं": "M", "ः": "H", "ँ": ".N"}, "virama": {"्": ""}, "consonants": {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "~N", "च": "ch", "छ": "Ch", "ज": "j", "झ": "jh", "ञ": "~n", "ट": "T", "ठ": "Th", "ड": "D", "ढ": "Dh", "ण": "N", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "Sh", "स": "s", "ह": "h", "ळ": "L", "क्ष": "kSh", "ज्ञ": "j~n"}, "symbols": {"०": "0", "१": "1", "२": "2", "३": "3", "४": "4", "५": "5", "६": "6", "७": "7", "८": "8", "९": "9", "ॐ": "OM", "ऽ": ".a", "।": "|", "॥": "||"}, "candra": {"ॅ": ".c"}, "zwj": {"‍": "{}"}, "skip": {"": "_"}, "accents": {"॑": "\\'", "॒": "\\_"}, "extra_consonants": {"क़": "q", "ख़": "K", "ग़": "G", "ज़": "z", "ड़": ".D", "ढ़": ".Dh", "फ़": "f", "य़": "Y", "ऱ": "R", "ऴ": "zh"}, "alternates": {"A": ["aa"], "I": ["ii", "ee"], "U": ["uu", "oo"], "RRi": ["R^i"], "RRI": ["R^I"], "LLi": ["L^i"], "LLI": ["L^I"], "M": [".m", ".n"], "~N": ["N^"], "ch": ["c"], "Ch": ["C", "chh"], "~n": ["JN"], "v": ["w"], "Sh": ["S", "shh"], "kSh": ["kS", "x"], "j~n": ["GY", "dny"], "OM": ["AUM"], "\\_": ["\\`"], "\\_H": ["\\`H"], "\\'M": ["\\'.m", "\\'.n"], "\\_M": ["\\_.m", "\\_.n", "\\`M", "\\`.m", "\\`.n"], ".a": ["~"], "|": ["."], "||": [".."], "z": ["J"]}, "isRomanScheme": true};
schemes.itrans_dravidian = {"vowels": {"अ": "a", "आ": "A", "इ": "i", "ई": "I", "उ": "u", "ऊ": "U", "ऋ": "RRi", "ॠ": "RRI", "ऌ": "LLi", "ॡ": "LLI", "ऎ": "e", "ए": "E", "ऐ": "ai", "ऒ": "o", "ओ": "O", "औ": "au"}, "yogavaahas": {"ं": "M", "ः": "H", "ँ": ".N"}, "virama": {"्": ""}, "consonants": {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "~N", "च": "ch", "छ": "Ch", "ज": "j", "झ": "jh", "ञ": "~n", "ट": "T", "ठ": "Th", "ड": "D", "ढ": "Dh", "ण": "N", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "Sh", "स": "s", "ह": "h", "ळ": "L", "क्ष": "kSh", "ज्ञ": "j~n"}, "symbols": {"०": "0", "१": "1", "२": "2", "३": "3", "४": "4", "५": "5", "६": "6", "७": "7", "८": "8", "९": "9", "ॐ": "OM", "ऽ": ".a", "।": "|", "॥": "||"}, "candra": {"ॅ": ".c"}, "zwj": {"‍": "{}"}, "skip": {"": "_"}, "accents": {"॑": "\\'", "॒": "\\_"}, "extra_consonants": {"क़": "q", "ख़": "K", "ग़": "G", "ज़": "z", "ड़": ".D", "ढ़": ".Dh", "फ़": "f", "य़": "Y", "ऱ": "R", "ऴ": "zh", "ऩ": "n2"}, "alternates": {"A": ["aa"], "I": ["ii", "ee"], "U": ["uu", "oo"], "RRi": ["R^i"], "RRI": ["R^I"], "LLi": ["L^i"], "LLI": ["L^I"], "M": [".m", ".n"], "~N": ["N^"], "ch": ["c"], "Ch": ["C", "chh"], "~n": ["JN"], "v": ["w"], "Sh": ["S", "shh"], "kSh": ["kS", "x"], "j~n": ["GY", "dny"], "OM": ["AUM"], "\\_": ["\\`"], "\\_H": ["\\`H"], "\\'M": ["\\'.m", "\\'.n"], "\\_M": ["\\_.m", "\\_.n", "\\`M", "\\`.m", "\\`.n"], ".a": ["~"], "|": ["."], "||": [".."], "z": ["J"]}, "isRomanScheme": true};
schemes.optitrans = {"vowels": {"अ": "a", "आ": "A", "इ": "i", "ई": "I", "उ": "u", "ऊ": "U", "ऋ": "R", "ॠ": "RR", "ऌ": "LLi", "ॡ": "LLI", "ऎ": "E", "ए": "e", "ऐ": "ai", "ऒ": "O", "ओ": "o", "औ": "au", "ऍ": "ea", "ऑ": "oa"}, "yogavaahas": {"ं": "M", "ः": "H", "ँ": ".N", "ᳵ": "kH", "ᳶ": "pH"}, "virama": {"्": ""}, "consonants": {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "~N", "च": "ch", "छ": "Ch", "ज": "j", "झ": "jh", "ञ": "~n", "ट": "T", "ठ": "Th", "ड": "D", "ढ": "Dh", "ण": "N", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "Sh", "स": "s", "ह": "h", "ळ": "L", "क्ष": "x", "ज्ञ": "jn"}, "symbols": {"०": "0", "१": "1", "२": "2", "३": "3", "४": "4", "५": "5", "६": "6", "७": "7", "८": "8", "९": "9", "ॐ": "OM", "ऽ": ".a", "।": "|", "॥": "||"}, "candra": {"ॅ": ".c"}, "zwj": {"‍": "{}"}, "skip": {"": "_"}, "accents": {"॑": "\\'", "॒": "\\_"}, "extra_consonants": {"क़": "q", "ख़": ".kh", "ग़": ".g", "ज़": "z", "ड़": ".D", "ढ़": ".Dh", "फ़": "f", "य़": "Y", "ऱ": ".Rh", "ऴ": ".L"}, "alternates": {"A": ["aa"], "I": ["ii", "ee"], "U": ["uu"], "R": ["RRi", "R^i"], "RR": ["RRI", "R^I"], "LLi": ["L^i"], "LLI": ["L^I"], "M": [".m", ".n"], "ch": ["c"], "Ch": ["C"], "jh": ["J"], "ph": ["P"], "bh": ["B"], "~n": ["JN"], "v": ["w"], "Sh": ["S"], "x": ["kS", "ksh"], "jn": ["GY", "dny"], "|": ["."], "||": [".."], "z": ["J"]}, "shortcuts": {"~nc": "nc", "~nC": "nC", "~nj": "nj", "~nJ": "nJ", "~Nk": "nk", "~NK": "nK", "~Ng": "ng", "~NG": "nG", "~Nx": "nx"}, "isRomanScheme": true};
schemes.optitrans_dravidian = {"vowels": {"अ": "a", "आ": "A", "इ": "i", "ई": "I", "उ": "u", "ऊ": "U", "ऋ": "R", "ॠ": "RR", "ऌ": "LLi", "ॡ": "LLI", "ऎ": "e", "ए": "E", "ऐ": "ai", "ऒ": "o", "ओ": "O", "औ": "au", "ऍ": "ea", "ऑ": "oa"}, "yogavaahas": {"ं": "M", "ः": "H", "ँ": ".N", "ᳵ": "kH", "ᳶ": "pH"}, "virama": {"्": ""}, "consonants": {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "~N", "च": "ch", "छ": "Ch", "ज": "j", "झ": "jh", "ञ": "~n", "ट": "T", "ठ": "Th", "ड": "D", "ढ": "Dh", "ण": "N", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "Sh", "स": "s", "ह": "h", "ळ": "L", "क्ष": "x", "ज्ञ": "jn"}, "symbols": {"०": "0", "१": "1", "२": "2", "३": "3", "४": "4", "५": "5", "६": "6", "७": "7", "८": "8", "९": "9", "ॐ": "OM", "ऽ": ".a", "।": "|", "॥": "||"}, "candra": {"ॅ": ".c"}, "zwj": {"‍": "{}"}, "skip": {"": "_"}, "accents": {"॑": "\\'", "॒": "\\_"}, "extra_consonants": {"क़": ".k", "ख़": "q", "ग़": ".g", "ज़": "z", "ड़": ".D", "ढ़": ".Dh", "फ़": "f", "य़": "Y", "ऱ": ".Rh", "ऴ": ".L", "ऩ": "n2"}, "alternates": {"A": ["aa"], "I": ["ii", "ee"], "U": ["uu"], "R": ["RRi", "R^i"], "RR": ["RRI", "R^I"], "LLi": ["L^i"], "LLI": ["L^I"], "M": [".m", ".n"], "ch": ["c"], "Ch": ["C"], "jh": ["J"], "ph": ["P"], "bh": ["B"], ".L": ["LH"], "~n": ["JN"], "v": ["w"], "Sh": ["S"], "x": ["kS", "ksh"], "jn": ["GY", "dny"], "|": ["."], "||": [".."], "z": ["J"]}, "shortcuts": {"~nc": "nc", "~nC": "nC", "~nj": "nj", "~nJ": "nJ", "~Nk": "nk", "~NK": "nK", "~Ng": "ng", "~NG": "nG", "~Nx": "nx"}, "isRomanScheme": true};
schemes.hk = {"vowels": {"अ": "a", "आ": "A", "इ": "i", "ई": "I", "उ": "u", "ऊ": "U", "ऋ": "R", "ॠ": "RR", "ऌ": "lR", "ॡ": "lRR", "ऎ": "è", "ए": "e", "ऐ": "ai", "ऒ": "ò", "ओ": "o", "औ": "au"}, "yogavaahas": {"ं": "M", "ः": "H", "ँ": "~"}, "virama": {"्": ""}, "consonants": {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "G", "च": "c", "छ": "ch", "ज": "j", "झ": "jh", "ञ": "J", "ट": "T", "ठ": "Th", "ड": "D", "ढ": "Dh", "ण": "N", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "z", "ष": "S", "स": "s", "ह": "h", "ळ": "L", "क्ष": "kS", "ज्ञ": "jJ"}, "symbols": {"०": "0", "१": "1", "२": "2", "३": "3", "४": "4", "५": "5", "६": "6", "७": "7", "८": "8", "९": "9", "ॐ": "OM", "ऽ": "'", "।": "|", "॥": "||"}, "extra_consonants": {"क़": "q", "ख़": "qh", "ग़": "g2", "ज़": "z2", "ड़": "r3", "ढ़": "r3h", "फ़": "f", "य़": "Y", "ऱ": "r2", "ऴ": "zh", "ऩ": "n2"}, "isRomanScheme": true};
schemes.iast = {"vowels": {"अ": "a", "आ": "ā", "इ": "i", "ई": "ī", "उ": "u", "ऊ": "ū", "ऋ": "ṛ", "ॠ": "ṝ", "ऌ": "ḷ", "ॡ": "ḹ", "ऎ": "è", "ए": "e", "ऐ": "ai", "ऒ": "ò", "ओ": "o", "औ": "au"}, "yogavaahas": {"ं": "ṃ", "ः": "ḥ", "ँ": "~", "ꣳ": "m̐"}, "virama": {"्": ""}, "accents": {"॑": "̭", "॒": "̱", "᳙": "̀", "᳓": "́", "꣡": "¹", "꣢": "²", "꣣": "³", "꣤": "⁴", "꣥": "⁵", "꣦": "⁶", "꣧": "⁷", "꣨": "⁸", "꣩": "⁹", "꣪": "ᵃ", "꣫": "ᵘ", "꣬": "ᵏ", "꣭": "ⁿ", "꣮": "ᵖ", "꣯": "ʳ", "꣰": "ᵛ", "꣱": "ˢ"}, "consonants": {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "ṅ", "च": "c", "छ": "ch", "ज": "j", "झ": "jh", "ञ": "ñ", "ट": "ṭ", "ठ": "ṭh", "ड": "ḍ", "ढ": "ḍh", "ण": "ṇ", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v", "श": "ś", "ष": "ṣ", "स": "s", "ह": "h", "ळ": "ḻ", "क्ष": "kṣ", "ज्ञ": "jñ"}, "symbols": {"०": "0", "१": "1", "२": "2", "३": "3", "४": "4", "५": "5", "६": "6", "७": "7", "८": "8", "९": "9", "ॐ": "oṃ", "ऽ": "'", "।": "|", "॥": "||"}, "extra_consonants": {"क़": "q", "ख़": "k͟h", "ग़": "ġ", "ज़": "z", "ड़": "r̤", "ढ़": "r̤h", "फ़": "f", "य़": "ẏ", "ऱ": "ṟ", "ऴ": "l̤", "ऩ": "ṉ"}, "alternates": {"m̐": ["ṁ", "ṁ"], "ī": ["ï"], "ṃ": ["ṃ"], "ḥ": ["ḥ"], "ṭ": ["ṭ"], "ṭh": ["ṭh"], "ḍ": ["ḍ"], "ḍh": ["ḍh"], "ṇ": ["ṇ"], "ṣ": ["ṣ"], "ṅ": ["ṅ"], "ñ": ["ñ"], "ṛ": ["r̥", "ṛ"], "ṝ": ["ṝ", "r̥̄", "r̥̄", "ṝ", "ṝ"], "́": ["¹"]}, "accented_vowel_alternates": {"á": ["á"], "é": ["é"], "í": ["í"], "aí": ["aí"], "ó": ["ó"], "ú": ["ú"], "aú": ["aú"], "à": ["à"], "è": ["è"], "ì": ["ì"], "aì": ["aì"], "ò": ["ò"], "ù": ["ù"], "aù": ["aù"], "ṛ̀": ["r̥̀", "ṛ̀"]}, "isRomanScheme": true};
const devanagariVowelToMarks = {"आ": "ा", "इ": "ि", "ई": "ी", "उ": "ु", "ऊ": "ू", "ऋ": "ृ", "ॠ": "ॄ", "ऌ": "ॢ", "ॡ": "ॣ", "ऎ": "ॆ", "ए": "े", "ऐ": "ै", "ऒ": "ॊ", "ओ": "ो", "औ": "ौ", "ऍ": "ॅ", "ऑ": "ॉ"};
/**
 * Sanscript
 *
 * Sanscript is a Sanskrit transliteration library. Currently, it supports
 * other Indian languages only incidentally.
 *
 * License: MIT
 */

function exportSanscriptSingleton (global, schemes, devanagariVowelToMarks) {
    "use strict";

    const Sanscript = {};
    // First, we define the Sanscript singleton, with its variables and methods.
    Sanscript.defaults = {
        "skip_sgml"            : false,
        "syncope"              : false,
        "preferred_alternates" : {},
    };

    const DETECTION_PATTERNS = {
        SCHEMES : [
            ['Bengali', 0x0980],
            ['Devanagari', 0x0900],
            ['Gujarati', 0x0a80],
            ['Gurmukhi', 0x0a00],
            ['Kannada', 0x0c80],
            ['Malayalam', 0x0d00],
            ['Oriya', 0x0b00],
            ['Tamil', 0x0b80],
            ['Telugu', 0x0c00],
            ['HK', null],
            ['IAST', null],
            ['ITRANS', null],
            ['Kolkata', null],
            ['SLP1', null],
            ['Velthuis', null],
        ],
        // Start of the Devanagari block.
        BRAHMIC_FIRST_CODE_POINT : 0x0900,

        // End of the Malayalam block.
        BRAHMIC_LAST_CODE_POINT : 0x0d7f,

        // Match on special Roman characters
        RE_IAST_OR_KOLKATA_ONLY : /[āīūṛṝḷḹēōṃḥṅñṭḍṇśṣḻ]/,

        // Match on chars shared by ITRANS and Velthuis
        RE_ITRANS_OR_VELTHUIS_ONLY : /aa|ii|uu|~n/,

        // Match on ITRANS-only
        RE_ITRANS_ONLY : /ee|oo|\^[iI]|RR[iI]|L[iI]|~N|N\^|Ch|chh|JN|sh|Sh|\.a/,

        // Match on Kolkata-specific Roman characters
        RE_KOLKATA_ONLY : /[ēō]/,

        // Match on SLP1-only characters and bigrams
        RE_SLP1_ONLY : RegExp(['[fFxXEOCYwWqQPB]|kz|Nk|Ng|tT|dD|Sc|Sn|',
            '[aAiIuUfFxXeEoO]R|',
            'G[yr]|(\\W|^)G'].join('')),

        // Match on Velthuis-only characters
        RE_VELTHUIS_ONLY : /\.[mhnrlntds]|"n|~s/,

    };

    /**
     * Detect the transliteration scheme of the given text
     * @param {string} text - Input text to analyze
     * @returns {string} - Detected scheme name or 'Unknown'
     */
    Sanscript.detect = function (text) {
        const Scheme = {};
        for (let i = 0; i < DETECTION_PATTERNS.SCHEMES.length; i++) {
            const value = DETECTION_PATTERNS.SCHEMES[i][0];
            Scheme[value] = value;
        }
        // Schemes sorted by Unicode code point. Ignore schemes with none defined.
        const BLOCKS = DETECTION_PATTERNS.SCHEMES
            .filter(function (x) {
                return x[1];
            })  // keep non-null
            .sort(function (x, y) {
                return y[1] - x[1];
            });  // sort by code point
        // Brahmic schemes are all within a specific range of code points.
        for (let i = 0; i < text.length; i++) {
            const L = text[i];
            const code = L.charCodeAt(L);
            if (code >= DETECTION_PATTERNS.BRAHMIC_FIRST_CODE_POINT && code <= DETECTION_PATTERNS.BRAHMIC_LAST_CODE_POINT) {
                for (let j = 0; j < BLOCKS.length; j++) {
                    const block = BLOCKS[j];
                    if (code >= block[1]) {
                        return block[0];
                    }
                }
            }
        }

        // Romanizations
        if (DETECTION_PATTERNS.RE_IAST_OR_KOLKATA_ONLY.test(text)) {
            if (DETECTION_PATTERNS.RE_KOLKATA_ONLY.test(text)) {
                return Scheme.Kolkata;
            }
            return Scheme.IAST;
        }

        if (DETECTION_PATTERNS.RE_ITRANS_ONLY.test(text)) {
            return Scheme.ITRANS;
        }

        if (DETECTION_PATTERNS.RE_SLP1_ONLY.test(text)) {
            return Scheme.SLP1;
        }

        if (DETECTION_PATTERNS.RE_VELTHUIS_ONLY.test(text)) {
            return Scheme.Velthuis;
        }

        if (DETECTION_PATTERNS.RE_ITRANS_OR_VELTHUIS_ONLY.test(text)) {
            return Scheme.ITRANS;
        }

        return Scheme.HK;

    };


    /* Schemes
     * =======
     * Schemes are of two kinds: "Brahmic" and "roman." "Brahmic" schemes
     * describe abugida scripts found in India. "Roman" schemes describe
     * manufactured alphabets that are meant to describe or encode Brahmi
     * scripts. Abugidas and alphabets are processed by separate algorithms
     * because of the unique difficulties involved with each.
     *
     * Brahmic consonants are stated without a virama. Roman consonants are
     * stated without the vowel 'a'.
     *
     * (Since "abugida" is not a well-known term, Sanscript uses "Brahmic"
     * and "roman" for clarity.)
     */
    Sanscript.schemes = schemes;
    // Set of names of schemes
    const romanSchemes = {};

    // object cache
    let cache = {};

    /**
     * Add a Brahmic scheme to Sanscript.
     *
     * Schemes are of two types: "Brahmic" and "roman". Brahmic consonants
     * have an inherent vowel sound, but roman consonants do not. This is the
     * main difference between these two types of scheme.
     *
     * A scheme definition is an object ("{}") that maps a group name to a
     * list of characters. For illustration, see the "devanagari" scheme at
     * the top of this file.
     *
     * You can use whatever group names you like, but for the best results,
     * you should use the same group names that Sanscript does.
     *
     * @param name    the scheme name
     * @param scheme  the scheme data itself. This should be constructed as
     *                described above.
     */
    Sanscript.addBrahmicScheme = function (name, scheme) {
        Sanscript.schemes[name] = scheme;
    };

    /**
     * Add a roman scheme to Sanscript.
     *
     * See the comments on Sanscript.addBrahmicScheme. The "vowel_marks" field
     * can be omitted.
     *
     * @param name    the scheme name
     * @param scheme  the scheme data itself
     */
    Sanscript.addRomanScheme = function (name, scheme) {
        if (!("vowel_marks" in scheme)) {
            scheme.vowel_marks = {};
            for (const [key, value] of Object.entries(scheme.vowels)) {
                if (key != "अ") {
                    scheme.vowel_marks[devanagariVowelToMarks[key]] = value;
                }
            }
        }
        Sanscript.schemes[name] = scheme;
        romanSchemes[name] = true;
    };


    // Set up various schemes
    (function () {
        // Set up roman schemes
        const capitalize = function (text) {
            return text.charAt(0).toUpperCase() + text.substring(1, text.length);
        };
        const addCapitalAlternates = function (codeList, alternatesMap) {
            for (const v of codeList) {
                const initAlternatesList = alternatesMap[v] || [];
                let alternatesList = initAlternatesList;
                alternatesList = alternatesList.concat(capitalize(v));
                for (const alternate of initAlternatesList) {
                    alternatesList = alternatesList.concat(capitalize(alternate));
                }
                alternatesMap[v] = alternatesList;
            }
        };
        addCapitalAlternates(Object.values(schemes.iast.vowels), schemes.iast.alternates);
        addCapitalAlternates(Object.values(schemes.iast.consonants), schemes.iast.alternates);
        addCapitalAlternates(Object.values(schemes.iast.extra_consonants), schemes.iast.alternates);
        addCapitalAlternates(["oṃ"], schemes.iast.alternates);
        addCapitalAlternates(Object.values(schemes.kolkata_v2.vowels), schemes.kolkata_v2.alternates);
        addCapitalAlternates(Object.values(schemes.kolkata_v2.consonants), schemes.kolkata_v2.alternates);
        addCapitalAlternates(Object.values(schemes.kolkata_v2.extra_consonants), schemes.kolkata_v2.alternates);

        addCapitalAlternates(Object.values(schemes.iso.vowels), schemes.iso.alternates);
        addCapitalAlternates(Object.values(schemes.iso.consonants), schemes.iso.alternates);
        addCapitalAlternates(Object.values(schemes.iso.extra_consonants), schemes.iso.alternates);
        addCapitalAlternates(["ōṁ"], schemes.iso.alternates);

        // These schemes already belong to Sanscript.schemes. But by adding
        // them again with `addRomanScheme`, we automatically build up
        // `romanSchemes` and define a `vowel_marks` field for each one.
        for (const [schemeName, scheme] of Object.entries(schemes)) {
            if (scheme.isRomanScheme) {
                Sanscript.addRomanScheme(schemeName, scheme);
            }
        }

    }());

    /**
     * Create a map from every character in `from` to its partner in `to`.
     * Also, store any "marks" that `from` might have.
     *
     * @param from     input scheme
     * @param to       output scheme
     * @param options  scheme options
     */
    const makeMap = function (from, to, _options) {
        const consonants = {};
        const fromScheme = Sanscript.schemes[from];
        const letters = {};
        const tokenLengths = [];
        const marks = {};
        const accents = {};
        const toScheme = Sanscript.schemes[to];

        const alternates = fromScheme["alternates"] || {};

        for (const group in fromScheme) {
            if (!{}.hasOwnProperty.call(fromScheme, group)) {
                continue;
            }
            if (["alternates", "accented_vowel_alternates", "isRomanScheme"].includes(group)) {
                continue;
            }
            const fromGroup = fromScheme[group];
            const toGroup = toScheme[group];
            if (toGroup === undefined) {
                continue;
            }
            for (const [key, F] of Object.entries(fromGroup)) {
                let T = toGroup[key];
                if (T === undefined) {
                    continue;
                }
                if (T == "" && !["virama", "zwj", "skip"].includes(group)) {
                    T = F;
                }
                const alts = alternates[F] || [];
                const numAlts = alts.length;
                let j = 0;

                tokenLengths.push(F.length);
                for (j = 0; j < numAlts; j++) {
                    tokenLengths.push(alts[j].length);
                }

                if (group === "vowel_marks" || group === "virama") {
                    marks[F] = T;
                    for (j = 0; j < numAlts; j++) {
                        marks[alts[j]] = T;
                    }
                } else {
                    letters[F] = T;
                    for (j = 0; j < numAlts; j++) {
                        letters[alts[j]] = T;
                    }
                    if (group === "consonants" || group === "extra_consonants") {
                        consonants[F] = T;

                        for (j = 0; j < numAlts; j++) {
                            consonants[alts[j]] = T;
                        }
                    }
                    if (group === "accents") {
                        accents[F] = T;

                        for (j = 0; j < numAlts; j++) {
                            accents[alts[j]] = T;
                        }
                    }
                }
            }
        }

        if (fromScheme["accented_vowel_alternates"]) {
            for (const baseAccentedVowel of Object.keys(fromScheme["accented_vowel_alternates"])) {
                const synonyms = fromScheme.accented_vowel_alternates[baseAccentedVowel];
                for (const accentedVowel of synonyms) {
                    const baseVowel = baseAccentedVowel.substring(0, baseAccentedVowel.length - 1);
                    const sourceAccent = baseAccentedVowel[baseAccentedVowel.length - 1];
                    const targetAccent = accents[sourceAccent] || sourceAccent;
                    // Roman a does not map to any brAhmic vowel mark. Hence "" below.
                    marks[accentedVowel] = (marks[baseVowel] || "") + targetAccent;
                    if (!letters[baseVowel]) {
                        console.error(baseVowel, targetAccent, letters);
                    }
                    letters[accentedVowel] = letters[baseVowel].concat(targetAccent);
                }
            }
        }



        return {
            consonants     : consonants,
            accents        : accents,
            fromRoman      : fromScheme.isRomanScheme,
            letters        : letters,
            marks          : marks,
            maxTokenLength : Math.max.apply(Math, tokenLengths),
            toRoman        : toScheme.isRomanScheme,
            virama         : toScheme.virama["्"],
            toSchemeA      : toScheme.vowels["अ"],
            fromSchemeA    : fromScheme.vowels["अ"],
            from           : from,
            to             : to,
        };
    };

    /**
     * Transliterate from a romanized script.
     *
     * @param data     the string to transliterate
     * @param map      map data generated from makeMap()
     * @param options  transliteration options
     * @return         the finished string
     */
    const transliterateRoman = function (data, map, options) {
        const buf = [];
        const consonants = map.consonants;
        const dataLength = data.length;
        const letters = map.letters;
        const marks = map.marks;
        const maxTokenLength = map.maxTokenLength;
        const optSkipSGML = options.skip_sgml;
        const optSyncope = options.syncope;
        const toRoman = map.toRoman;
        const virama = map.virama;

        let hadConsonant = false;
        let tempLetter;
        let tempMark;
        let tokenBuffer = "";

        // Transliteration state. It's controlled by these values:
        // - `skippingSGML`: are we in SGML?
        // - `toggledTrans`: are we in a toggled region?
        //
        // We combine these values into a single variable `skippingTrans`:
        //
        //     `skippingTrans` = skippingSGML || toggledTrans;
        //
        // If (and only if) this value is true, don't transliterate.
        let skippingSGML = false;
        let skippingTrans = false;
        let toggledTrans = false;

        for (let i = 0, L; (L = data.charAt(i)) || tokenBuffer; i++) {
            // Fill the token buffer, if possible.
            const difference = maxTokenLength - tokenBuffer.length;
            if (difference > 0 && i < dataLength) {
                tokenBuffer += L;
                if (difference > 1) {
                    continue;
                }
            }

            // Match all token substrings to our map.
            for (let j = 0; j < maxTokenLength; j++) {
                const token = tokenBuffer.substr(0, maxTokenLength - j);

                if (skippingSGML === true) {
                    skippingSGML = (token !== ">");
                } else if (token === "<") {
                    skippingSGML = optSkipSGML;
                } else if (token === "##") {
                    toggledTrans = !toggledTrans;
                    tokenBuffer = tokenBuffer.substr(2);
                    break;
                }
                skippingTrans = skippingSGML || toggledTrans;
                if ((tempLetter = letters[token]) !== undefined && !skippingTrans) {
                    if (toRoman) {
                        buf.push(tempLetter);
                    } else {
                        // Handle the implicit vowel. Ignore 'a' and force
                        // vowels to appear as marks if we've just seen a
                        // consonant.
                        if (hadConsonant) {
                            if ((tempMark = marks[token])) {
                                buf.push(tempMark);
                            } else if (token !== map.fromSchemeA) {
                                buf.push(virama);
                                buf.push(tempLetter);
                            }
                        } else {
                            buf.push(tempLetter);
                        }
                        hadConsonant = token in consonants;
                    }
                    tokenBuffer = tokenBuffer.substr(maxTokenLength - j);
                    break;
                } else if (j === maxTokenLength - 1) {
                    if (hadConsonant) {
                        hadConsonant = false;
                        if (!optSyncope) {
                            buf.push(virama);
                        }
                    }
                    buf.push(token);
                    tokenBuffer = tokenBuffer.substr(1);
                    // 'break' is redundant here, "j == ..." is true only on
                    // the last iteration.
                }
            }
        }
        if (hadConsonant && !optSyncope) {
            buf.push(virama);
        }
        let result = buf.join("");
        const toScheme = schemes[map.to];
        if (!toRoman && Object.keys(map.accents).length > 0) {
            const pattern = new RegExp(`([${Object.values(map.accents).join("")}])([${Object.values(toScheme['yogavaahas']).join("")}])`, "g");
            result = result.replace(pattern, "$2$1");
        }

        return result;
    };

    /**
     * Transliterate from a Brahmic script.
     *
     * @param data     the string to transliterate
     * @param map      map data generated from makeMap()
     * @param options  transliteration options
     * @return         the finished string
     */
    const transliterateBrahmic = function (data, map, _options) {
        const buf = [];
        const consonants = map.consonants;
        const letters = map.letters;
        const marks = map.marks;
        const toRoman = map.toRoman;

        let danglingHash = false;
        let hadRomanConsonant = false;
        let temp;
        let skippingTrans = false;
        const toScheme = schemes[map.to];

        if (toRoman && Object.keys(map.accents).length > 0) {
            const pattern = new RegExp(`([${Object.values(toScheme['yogavaahas']).join("")}])([${Object.values(map.accents).join("")}])`, "g");
            data = data.replace(pattern, "$2$1");
        }

        for (let i = 0, L; (L = data.charAt(i)); i++) {
            // Toggle transliteration state
            if (L === "#") {
                if (danglingHash) {
                    skippingTrans = !skippingTrans;
                    danglingHash = false;
                } else {
                    danglingHash = true;
                }
                if (hadRomanConsonant) {
                    buf.push(map.toSchemeA);
                    hadRomanConsonant = false;
                }
                continue;
            } else if (skippingTrans) {
                buf.push(L);
                continue;
            }

            if ((temp = marks[L]) !== undefined) {
                buf.push(temp);
                hadRomanConsonant = false;
            } else {
                if (danglingHash) {
                    buf.push("#");
                    danglingHash = false;
                }
                if (hadRomanConsonant) {
                    buf.push(map.toSchemeA);
                    hadRomanConsonant = false;
                }

                // Push transliterated letter if possible. Otherwise, push
                // the letter itself.
                if ((temp = letters[L])) {
                    buf.push(temp);
                    hadRomanConsonant = toRoman && (L in consonants);
                } else {
                    buf.push(L);
                }
            }
        }
        if (hadRomanConsonant) {
            buf.push(map.toSchemeA);
        }
        return buf.join("");
    };

    /**
     * Transliterate from one script to another.
     *
     * @param data     the string to transliterate
     * @param from     the source script
     * @param to       the destination script
     * @param options  transliteration options
     * @return         the finished string
     */
    Sanscript.t = function (data, from, to, options) {
        if (!from) {
            from = Sanscript.detect(data).toLowerCase();
        }
        options = options || {};
        const cachedOptions = cache.options || {};
        const defaults = Sanscript.defaults;
        let hasPriorState = (cache.from === from && cache.to === to);
        let map;

        // Here we simultaneously build up an `options` object and compare
        // these options to the options from the last run.
        for (const key in defaults) {
            if ({}.hasOwnProperty.call(defaults, key)) {
                let value = defaults[key];
                if (key in options) {
                    value = options[key];
                }
                options[key] = value;

                // This comparison method is not generalizable, but since these
                // objects are associative arrays with identical keys and with
                // values of known type, it works fine here.
                if (value !== cachedOptions[key]) {
                    hasPriorState = false;
                }
            }
        }

        if (hasPriorState) {
            map = cache.map;
        } else {
            map = makeMap(from, to, options);
            cache = {
                from    : from,
                map     : map,
                options : options,
                to      : to,
            };
        }

        // Easy way out for "{\m+}", "\", and ".h".
        if (from === "itrans") {
            data = data.replace(/\{\\m\+\}/g, ".h.N");
            data = data.replace(/\.h/g, "");
            data = data.replace(/\\([^'`_]|$)/g, "##$1##");
        }
        if (from === "tamil_superscripted") {
            const pattern = "([" + Object.values(schemes["tamil_superscripted"]["vowel_marks"]).join("") + schemes["tamil_superscripted"]["virama"]["्"] + "॒॑" + "]+)([²³⁴])";
            data = data.replace(new RegExp(pattern, "g"), "$2$1");
            console.error("transliteration from tamil_superscripted not fully implemented!");
        }

        const fromShortcuts = schemes[from]["shortcuts"];
        // console.log(fromShortcuts);
        if (fromShortcuts) {
            for (const key in fromShortcuts) {
                const shortcut = fromShortcuts[key];
                if (key.includes(shortcut)) {
                    // An actually long "key" may already exist in the string
                    data = data.replace(key, shortcut);
                }
                data = data.replace(shortcut, key);
            }
        }

        let result = "";
        if (map.fromRoman) {
            result = transliterateRoman(data, map, options);
        } else {
            result = transliterateBrahmic(data, map, options);
        }
        // Apply shortcuts
        const toShortcuts = schemes[to]["shortcuts"];
        if (toShortcuts) {
            for (const key in toShortcuts) {
                const shortcut = toShortcuts[key];
                if (shortcut.includes(key)) {
                    // An actually long "shortcut" may already exist in the string
                    result = result.replace(shortcut, key);
                }
                result = result.replace(key, shortcut);
            }
        }
        if (to === "tamil_superscripted") {
            const pattern = "([²³⁴])([" + Object.values(schemes["tamil_superscripted"]["vowel_marks"]).join("") + schemes["tamil_superscripted"]["virama"]["्"] + "॒॑" + "]+)";
            result = result.replace(new RegExp(pattern, "g"), "$2$1");
        }

        if(typeof options.preferred_alternates[to] == "object") {
            const keys = Object.keys(options.preferred_alternates[to]);
            for (let i = 0; i< keys.length; i++)
            {
                result = result.split(keys[i]).join(options.preferred_alternates[to][keys[i]]);
            }
        }

        return result;
    };

    /**
     * A function to transliterate each word, for the benefit of script learners.
     *
     * @param data
     * @param from
     * @param to
     * @param options
     */
    Sanscript.transliterateWordwise = function (data, from, to, options) {
        options = options || {};
        const words = data.split(/\s+/);
        const word_tuples = words.map(function (word) {
            const result = Sanscript.t(word, from, to, options);
            return [word, result];
        });
        return word_tuples;
    };



    // Now that Sanscript is fully defined, we now safely export it for use elsewhere.
    // The below block was copied from https://www.npmjs.com/package/sanscript .
    // define seems to be a requirejs thing https://requirejs.org/docs/whyamd.html#amd .
    if (typeof define === "function" && define.amd) {
        define(function () {
            return Sanscript;
        });
    } else if (typeof exports !== "undefined") {
        if (typeof module !== "undefined" && module.exports) {
            exports = Sanscript;
            module.exports = Sanscript;
        }

        exports.Sanscript = Sanscript;
    } else {
        global.Sanscript = Sanscript;
    }
}

// The below comment avoids jslint failure.
/* global schemes devanagariVowelToMarks*/
exportSanscriptSingleton(this, schemes, devanagariVowelToMarks);
