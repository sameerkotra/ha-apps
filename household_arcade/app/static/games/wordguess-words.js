/* Household Arcade — Word Guess word lists. An original list written for this app (not copied from any other game).
   ANSWERS: everyday five-letter words a child of about ten would know; the secret word always comes from here.
   EXTRA: other real five-letter words that are accepted as guesses but never picked as the answer.
   Nothing rude, hateful or frightening is in either list; BLOCKED words are removed from both when the file loads. */
(function (root) {
  "use strict";
  var BLOCKED = "bitch whore sluts penis pussy boobs cunts fucks shits dicks nazis rapes raped hitler" ;
  var ANSWERS =
    "about above actor adult after again agree ahead alarm album alert alike alive allow alone along aloud " +
    "angle angry apart apple arrow aside atlas attic audio award aware awful bacon badge baker basic beach " +
    "beard beast begin being bench berry bikes birth black blade blame blank blast blaze blend bless blind " +
    "block bloom board boast boats bonus boost booth bored bound brain brake brand brave bread break brick " +
    "bride brief bring broad broke brook broom brown brush build bunch burst cabin cable camel candy canoe " +
    "carry carve catch cause chain chair chalk charm chart chase cheap check cheek cheer chess chest chief " +
    "child chill chime chips choir chunk cider civil claim class clean clear clerk click cliff climb cloak " +
    "clock close cloth cloud clown coach coast coins comet comic coral couch cough could count court cover " +
    "crack craft crane crash crawl crazy cream creek creep crisp cross crowd crown crumb crush curve cycle " +
    "daily dairy daisy dance dated dealt delay delta dense depth diary digit dingo dirty ditch dizzy dodge " +
    "doing donor doubt dough dozen draft drain drama drank drawn dream dress dried drift drill drink drive " +
    "drops drove drums dryer dusty eager eagle early earth easel eaten edges eight elbow elder elect elite " +
    "empty enemy enjoy enter entry equal error essay event every exact exams extra fable faint fairy faith " +
    "false fancy fares farms fault feast fence ferry fever fewer fiber field fifth fifty fight final first " +
    "flame flash flask fleet flesh flick fling float flock flood floor flour fluid flute focus foggy force " +
    "forge forth forty forum found frame fresh fried front frost fruit fuels funny fuzzy games gauge geese " +
    "ghost giant gifts given glass gleam globe glory glove glued goals goats going grace grade grain grand " +
    "grant grape graph grasp grass great green greet grill grind grips groom group grove grown guard guess " +
    "guest guide habit hairy handy happy harsh haste hatch haunt heads heard heart heavy hedge hello hence " +
    "herbs hiked hills hinge hobby holes honey honor hoped horse hotel hours house human humor hurry ideas " +
    "image imply index inner input irons issue ivory jelly jewel joint jolly judge juice jumps keeps kicks " +
    "kinds kings kites kneel knife knock known label labor lakes lamps lands large laser later laugh layer " +
    "leads leafy learn lease least leave legal lemon level lever light liked limit lines links lions lists " +
    "lived liver lobby local lodge logic loose lorry lover lower lucky lunch lungs magic major maker males " +
    "maple march marks marsh mason match maybe mayor meals means meant medal media meets melon mercy merge " +
    "merit messy metal meter might minor minus mixed model moist money month moral motor mound mount mouse " +
    "mouth moved movie music nails naive named nanny nasty naval needs nerve never newly night noise north " +
    "noses noted novel nurse oasis ocean offer often olive onion opens opera orbit order organ other otter " +
    "ought outer owner oxide paint pairs panel paper parks party pasta paste patch pause peace peach pearl " +
    "pedal penny perch phase phone photo piano picks piece pilot pinch pitch pizza place plain plane plans " +
    "plant plate plays plaza pluck plume point polar pools porch pouch pound power press price pride prime " +
    "print prize proof proud prove pulls pulse pumps punch pupil puppy purse queen quick quiet quilt quite " +
    "quote races radio rails rains raise rally ranch range rapid ratio reach react ready realm rebel refer " +
    "relax reply rider ridge right rings rinse risky river roast robin robot rocks rocky roles rolls roofs " +
    "rooms roots rough round route royal rugby ruler rural sadly safer sails salad sandy sauce saved scale " +
    "scarf scene scent scone score scout screw seals seats seeds sense serve seven shade shake shall shame " +
    "shape share shark sharp sheep sheet shelf shell shift shine shiny ships shirt shock shoes shone shook " +
    "shoot shops shore short shout shown sight silly since sings sixth sixty sized skate skill skins skirt " +
    "skull sleep slice slide slope small smart smell smile smoke snack snail snake sneak snore snowy soapy " +
    "solar solid solve songs sorry sorts souls sound south space spare spark speak speed spell spend spent " +
    "spice spicy spike spill spine spoon sport spots spray squad stack staff stage stain stair stake stamp " +
    "stand stare stars start state stays steak steam steel steep stems steps stern stick stiff still sting " +
    "stock stone stood stool stops store storm story stove straw strip stuck study stuff style sugar suite " +
    "sunny super swamp swans swear sweat sweep sweet swept swift swing sword table tails taken tales tamed " +
    "tanks tapes taste teach teams tears teddy teens teeth tells tempo tenth tents terms thank theme there " +
    "these thick thing think third those three threw throw thumb tiger tight tiles timer times tired title " +
    "toast today token tones tooth topic torch total touch tough towel tower towns toxic trace track trade " +
    "trail train trait trash tread treat trees trend trial tribe trick tried tries troop truck truly trunk " +
    "trust truth tubes tulip tunes turns tutor twice twins twist uncle under union unite unity until upper " +
    "upset urban usage usual valid value valve vases verse video views villa vines viola visit vital vivid " +
    "vocal voice voter wages wagon waist walks walls waltz wants warms waste watch water waves wears weary " +
    "weave weeds weeks weigh weird wells whale wheat wheel where which while whips white whole whose wider " +
    "widow width wills winds wings wiped wires witch woman women woods words works world worms worry worth " +
    "would wound wrist write wrong wrote yacht yards yearn years yeast yield young yours youth zebra zones";
  var EXTRA =
    "abbey abide abled abode abuse acorn acres acute adapt added adept admit adopt adore agent agile aging " +
    "agony aisle alley allot alloy aloft amaze amber amend ample amuse anger ankle annex antic anvil apron " +
    "arena argue arise armed aroma array arson ashen asked aspen atone avoid await awake awoke axiom bagel " +
    "baggy balmy banjo barge baron basin batch bathe baton beech beets beige belly below berth bingo birch " +
    "bison bland blare bleak blimp bliss bloat blown blues bluff blunt blurt blush boggy bogus bongo booty " +
    "borax bosom bossy botch boxer brace braid brash brass bravo brawl brine brink briny brisk broil broth " +
    "brute buddy budge buggy bugle bulge bulky bully bumpy burly burnt buyer cacti cadet cagey cairn camps " +
    "canal canny caper cargo carol carts cased caste cease cedar chant chaos chasm cheat chewy chick chide " +
    "chili chord chose churn cigar cinch circa clamp clang clash clasp clave cleat cleft clone clump coded " +
    "colon color comma conch cones cooks cools coped corgi corny corps costs crabs cramp crate crave crept " +
    "crest crime croak crock crone crook croon cruel crypt cubic cumin curly curry curse daunt davit dazed " +
    "debut decay decor decoy decry deity delve denim depot derby deter detox dials diner dingy disco ditto " +
    "diver divot dogma dolls donut dowdy downy dowry draws dread drier drone drool droop drown duchy dummy " +
    "dwarf dwell dying eased eaves ebony edict eerie eject elate elegy elope elude email ember emcee enact " +
    "ended endow ennui ensue envoy epoch equip erase erode erupt ethic evade evoke exalt excel exert exile " +
    "exist expat expel extol facet farce fatal fatty favor feign feint fella femur fetch fiery filet filly " +
    "filmy filth finch finer fishy fixer fizzy fjord flair flake flaky flare fleck flier flint flirt flora " +
    "flung flunk flush foamy foist folly foray forte foyer frail frank fraud freak freed freer friar frill " +
    "frisk frock frond froze frump fudge fungi funky furry gaffe gamer gamut gassy gaudy gavel gawky gayer " +
    "gecko geeky genie genre girth gizmo glade gland glare glaze glean glide glint gloat gloom gloss glyph " +
    "gnash gnome godly golem gooey goofy goose gorge gouge gourd grail grate gravy graze greed grief grime " +
    "gripe groan gross grout growl gruel gruff grunt guava guild guile guilt gummy gusto gusty gypsy halve " +
    "hands hardy harem harpy hazel heaps heave hefty heist helix hertz hippo hitch hoard hoary hoist holly " +
    "homer horde hound hovel hover howdy hubby huffy hulky humid humus hunch hunky husky hutch hyena hymns " +
    "icing ideal idiom idiot idler igloo imbue impel inane incur inept inert infer ingot inlay inlet irate " +
    "irony islet jaunt jazzy jerky jiffy jimmy joker jolts joust juicy jumbo jumpy junky juror kebab khaki " +
    "kiosk knack knave knead knelt knobs knoll koala kudos lanky lapel lapse larva latch lathe latte lawns " +
    "leach leaky leapt ledge leech leery lefty legit lemur liege lilac limbo linen liner lingo lithe llama " +
    "loamy lofty loner lords loser lotus louse lousy loyal lucid lumpy lunar lurch lurid lusty lying lymph " +
    "lyric macho madam madly mafia mambo mamma mango mangy manic manly manor marry masks mauve maxim mealy " +
    "meaty medic melee memos merry metro micro midst milky mimic mince minty mirth miser missy mocha modem " +
    "molar moldy mooch moody moose moped morph mossy motel motif motto mould mourn mover mucky mulch mummy " +
    "munch mural murky mushy musky musty myrrh nacho nadir nasal needy neigh nerdy newsy niche niece nifty " +
    "ninja ninth noble nodes noisy nomad noose nutty nylon oaken occur oddly offal older omega onset oozed " +
    "opine optic ounce outdo outgo ovens overt owing ozone paddy pagan paled palsy panda panic pansy papal " +
    "parka parry parse patio patsy payee peaky pecan penal perky pesky petal petty phlox piety piggy pinky " +
    "pious piper pique pithy pixel pixie plaid plank plead pleat plied plonk plumb plump plush poach podgy " +
    "poems poesy poise poker polka polyp poppy porky posed poser posse pouty prank prawn preen prick primp " +
    "privy probe prone prong prose proxy prude prune psalm puffy pulpy pupae purge pushy putty pygmy quack " +
    "quail quake qualm quart quash quasi query queue quirk quota radar radii rainy rajah raspy ratty ravel " +
    "raven rayon razor rebus recap recur reedy refit regal rehab reign relic remit renal renew repay repel " +
    "resin retch retro reuse revel rhino rhyme rifle rigid rigor riled rinks risen rival riven roach rodeo " +
    "rogue roomy rouge rowdy ruddy rumba rummy rumor rusty sabre saint salon salsa salty salve samba sassy " +
    "satin satyr saucy sauna savor savvy scald scalp scaly scamp scant scare scary scoff scold scoop scoot " +
    "scope scorn scour scowl scram scrap scrub scuba sedan seedy segue seize sepia serif serum setup sewer " +
    "shack shady shaft shaky shale shawl shear sheen shied shire shirk shoal shove showy shrub shrug shuck " +
    "shunt shyly siege sieve sigma silky silty sinew singe siren skied skiff skimp skulk slack slain slang " +
    "slant slash slate slaty sleek sleet slept slick slime slimy sling slink sloop slosh sloth slump slung " +
    "slunk slurp slush smack smash smear smelt smirk smite smock smoky smote snafu snare snarl sneer snide " +
    "snipe snoop snort snout snuff soggy soupy spade spasm spawn speck spelt spied spire spite splat splay " +
    "spoil spoke spoof spook spool spore spout spree sprig spunk spurn spurt squab squat squid stagy staid " +
    "stalk stall stark stave stead steed steer stein stilt stint stoic stoke stole stomp stony stoop stork " +
    "stout strap strew stung stunk stunt suave sudsy suede sulky sully sunup surer surge surly sushi swarm " +
    "swash swath swell swill swipe swirl swoon swoop syrup tabby taboo tacit tacky taffy taint talon tango " +
    "tangy taper tapir tardy tarot tarry taunt tawny teary tease tempt tenor tense tepid terra terse testy " +
    "thief thigh thorn throb thump thyme tidal tilde timid tinge tipsy tithe toady tonic tonne topaz toque " +
    "torso totem towed trawl tripe troll trope trout truce tryst tubby tuber tummy tunic turbo tweak tweed " +
    "tweet twerp twine twirl udder ulcer ultra umbra unarm uncut undue unfit unify unlit unmet unset untie " +
    "upend usher using usurp utter vague valet valor vault vaunt veers venom venue verge verve vicar vigil " +
    "vigor vinyl viper virus visor vista vodka vouch vowel vying wacky wader wafer wager waifs wails waken " +
    "wanly warty washy wasps watts wedge weedy weepy whack wharf whelp whiff whine whirl whisk whoop widen " +
    "wield wimpy wispy witty wives woken woody wooer woozy wordy wormy woven wrack wrath wreak wreck wring " +
    "wryly yodel yokel yummy zesty zippy";

  function clean(list) {
    var blocked = {}, seen = {};
    BLOCKED.split(" ").forEach(function (w) { blocked[w] = true; });
    return list.split(" ").filter(function (w) {
      if (w.length !== 5 || !/^[a-z]+$/.test(w) || blocked[w] || seen[w]) return false;
      seen[w] = true;
      return true;
    });
  }
  var answers = clean(ANSWERS), extra = clean(EXTRA), set = {};
  answers.forEach(function (w) { set[w] = true; });
  extra = extra.filter(function (w) { return !set[w]; });
  extra.forEach(function (w) { set[w] = true; });

  root.WordGuessWords = {
    ANSWERS: answers, EXTRA: extra, BLOCKED: BLOCKED.split(" "),
    isWord: function (w) { return set[String(w).toLowerCase()] === true; },
  };
  if (typeof module !== "undefined" && module.exports) module.exports = root.WordGuessWords;
})(typeof window !== "undefined" ? window : globalThis);
