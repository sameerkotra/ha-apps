/* Household Arcade — Word Search word lists: ten themes of everyday words, picked by length for each age group
   (see wordsearch-logic.js). Original lists written for this app: plain words a child would know, nothing rude
   or frightening. A theme has words of all lengths so that every age group finds enough. */
(function (root) {
  "use strict";
  var THEMES = [
    { id: "animals", name: "Animals", words:
      "ant ape bat bee cat cow cub dog fox hen owl pig pup rat yak bear bird crab deer duck fish frog " +
      "goat hare lamb lion mole moth newt seal swan toad wolf camel eagle horse koala mouse otter panda " +
      "shark sheep snail tiger whale zebra badger beaver donkey falcon lizard monkey parrot rabbit " +
      "spider turtle buffalo dolphin giraffe gorilla hamster leopard octopus penguin elephant hedgehog " +
      "kangaroo squirrel" },
    { id: "food", name: "Food", words:
      "bun egg fig ham jam nut oat pie tea cake corn milk pear plum rice soup apple bacon beans bread " +
      "candy flour grape honey juice lemon mango melon onion pasta peach pizza salad sugar toast banana " +
      "burger butter carrot cereal cheese cookie noodle orange potato tomato berries biscuit pancake " +
      "popcorn pudding pumpkin spinach broccoli cucumber sandwich" },
    { id: "nature", name: "Nature", words:
      "dew fog ice mud sea sky sun bush fern hill lake leaf moon moss pond rain rock rose sand seed " +
      "snow star tree wave wind beach cliff cloud flood grass river storm breeze canyon desert flower " +
      "forest island jungle meadow shadow stream sunset valley blossom glacier rainbow sunrise thunder " +
      "tornado volcano mountain waterfall" },
    { id: "home", name: "Home", words:
      "bed cup fan jug mat pan pot rug bath bowl desk door fork lamp sink sofa broom chair clock knife " +
      "plate shelf spoon stove table towel basket candle carpet drawer fridge garage garden hammer " +
      "kettle ladder mirror pillow stairs window bedroom blanket blender cabinet curtain cushion " +
      "freezer kitchen toaster doorbell" },
    { id: "school", name: "School", words:
      "art bag ink map pen book desk glue poem quiz test board chalk class lunch maths paint paper " +
      "ruler story crayon eraser lesson letter marker number pencil recess report diploma grammar " +
      "history library project reading science student teacher writing alphabet backpack calendar " +
      "homework notebook scissors sentence spelling classroom sharpener playground" },
    { id: "sports", name: "Sports", words:
      "bat gym hop jog net run ball game goal judo jump kick race swim team yoga coach court field " +
      "medal pitch rugby score skate track boxing diving golfer helmet hockey karate player racket " +
      "rowing skiing soccer tennis trophy winner archery bowling cricket cycling netball referee " +
      "sailing stadium surfing whistle baseball football marathon" },
    { id: "space", name: "Space", words:
      "sky sun mars moon star alien blast comet earth lunar milky orbit pluto probe rover solar venus " +
      "cosmos crater galaxy launch meteor module nebula planet rocket saturn uranus capsule eclipse " +
      "gravity jupiter mercury neptune shuttle station asteroid universe astronaut countdown moonlight " +
      "satellite spaceship starlight telescope" },
    { id: "ocean", name: "Ocean", words:
      "cod eel fin net ray sea sun wet boat clam crab dive reef sand seal ship surf swim tide wave " +
      "beach coral pearl shark shell squid whale anchor harbor island lagoon oyster pirate sailor " +
      "voyage walrus captain current dolphin lobster mermaid octopus penguin seaweed seahorse starfish " +
      "treasure jellyfish lighthouse" },
    { id: "music", name: "Music", words:
      "pop band beat bell clap drum folk harp horn jazz note rock sing song tuba tune album banjo cello " +
      "choir dance flute opera organ piano pitch rhyme tempo viola chorus guitar lyrics melody octave " +
      "rhythm singer violin concert harmony quartet speaker trumpet whistle clarinet playlist recorder " +
      "trombone orchestra saxophone xylophone microphone" },
    { id: "travel", name: "Travel", words:
      "bag bus car jet map van bike boat hike rail ride road ship taxi tent tram trip beach cabin cycle " +
      "ferry guide hotel metro plane train truck visit bridge cruise flight harbor ticket tunnel " +
      "airport camping compass driving highway holiday journey luggage station tourist backpack " +
      "passport suitcase adventure" },
  ];
  THEMES.forEach(function (t) { t.words = t.words.split(" "); });
  var WordSearchWords = { THEMES: THEMES };
  if (typeof module === "object" && module.exports) module.exports = WordSearchWords; else root.WordSearchWords = WordSearchWords;
})(typeof window !== "undefined" ? window : globalThis);
