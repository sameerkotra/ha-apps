/* Household Arcade — Type Rain word list. An original, family-friendly list written for this app: everyday words a
   child learning to type would know, 3 to 10 letters, letters a–z only. typerain-logic.js sorts them by length and
   picks from the seed. (Little ones' mode uses single letters instead.) */
(function (root) {
  "use strict";
  var WORDS = (
    // 3 letters
    "ant ape arm art bag bat bed bee box boy bud bus cab cap car cat cow cup cub dad day den dig dog dot dry " +
    "ear egg elf elm end eye fan fig fin fix fly fog fox fun fur gap gem gum hat hay hen hid hill hop hot hug " +
    "hut ice ink jam jar jet jog joy key kid kit lab lap leg lid log mat map mix mom mop mud mug nap net new nut " +
    "oak oar owl pan paw pea pen pet pie pig pin pit pod pot pup rag ram rat red rib rod row rug run sad saw sea " +
    "set sip sit sky sun tag tan tap tea ten tin toe top toy tub tug van web wig win yak yam yes zip zoo " +
    // 4 letters
    "aunt baby bake ball band bark barn bath bead bear bell bike bird blue boat bone book boot bowl bulb cake " +
    "calm camp card cave chin city clap clay coat cold corn crab cube cute dart deer desk dish dive doll door " +
    "dove drum duck dust easy farm fast fern fish flag foal foot fork frog game gate gift girl glad glue goat " +
    "gold golf good grin hand harp hike hill home hood hoop horn idea iron jump kind king kite kiwi lake lamb " +
    "lamp leaf lime lion load long loud mask meal milk mint moon moss nest nose note oven pear pond pony pool " +
    "rain rest ring road rock roof rope rose sand seal seed ship shoe shop sing snow soap sock sofa song soup " +
    "star swan swim tail team tent tide time toad town tree tune vase wave well whip wind wing wise wolf wool " +
    "yard yarn year zero zoom " +
    // 5 letters
    "acorn apple bacon badge beach berry blank bloom board brave bread brick broom brush cabin camel candy " +
    "chair chalk cheer chess chick cloud clown coast comet coral crane crown daisy dance dream drink eagle " +
    "earth fairy feast field flame float flute frost fruit giant glass globe glove grape grass happy heart " +
    "honey horse house juice jelly koala laugh lemon light lunch magic mango maple melon money mouse music " +
    "night ocean onion otter paint panda paper party peach pearl piano pilot pizza plant plate plums pound " +
    "puppy queen quiet radio raven river robin robot rocks salad scarf seeds shark sheep shell shine skate " +
    "sleep smile snail snack spoon sport stamp stars storm story sugar sunny swing table teddy tiger toast " +
    "torch tower train treat truck tulip uncle video wagon water whale wheat wheel world zebra " +
    // 6 letters
    "almond animal autumn banana basket beaver bottle branch breeze bridge bubble bucket butter button camera " +
    "candle carrot castle cherry circle cookie copper cotton crayon dragon drawer feather finger flower forest " +
    "friend garden ginger guitar hammer helmet hiking island jacket jungle kettle kitten ladder lizard marble " +
    "meadow mitten monkey muffin needle noodle orange parrot pencil pepper pickle pillow planet pocket potato " +
    "puzzle rabbit rocket saddle sailor school shadow silver singer sister spider spring squash summer sunset " +
    "teapot tennis ticket tomato turtle valley violin walrus window winter yellow " +
    // 7 letters
    "acrobat airport balloon bedroom bicycle biscuit blanket cabbage cartoon chicken cobweb compass cupcake " +
    "dolphin drawing feather fireman flannel giraffe glitter hamster harbour harvest holiday iceberg jasmine " +
    "kitchen ladybug lantern library lobster meadows morning mustard octopus orchard oatmeal painter pancake " +
    "panther parsley peacock pelican penguin picture pumpkin pyramid rainbow raccoon reading sandals science " +
    "seaweed shelter snowman sparrow station sweater teacher thunder tractor trumpet unicorn volcano walnuts " +
    "weather whistle " +
    // 8 letters and more
    "airplane alphabet aquarium avocado backpack baseball birthday blueberry bluebird building butterfly " +
    "calendar campfire carousel chipmunk chocolate classroom clubhouse cucumber daffodil dinosaur doorbell " +
    "dragonfly elephant evergreen fireworks flamingo football goldfish grandpa grandma grasshopper hedgehog " +
    "homework horseshoe jellyfish kangaroo keyboard lemonade lighthouse marigold mushroom notebook octagon " +
    "painting pancakes peppermint pineapple playground popcorn postcard rainfall raspberry sandwich seashell " +
    "skeleton snowflake sparkle spinach squirrel starfish strawberry sunflower sunshine surprise telescope " +
    "tortoise treasure triangle umbrella vacation waterfall watermelon wildflower woodpecker"
  ).split(" ").filter(function (w) { return /^[a-z]{3,10}$/.test(w); });
  // one copy of each word
  var seen = {}, list = [];
  WORDS.forEach(function (w) { if (!seen[w]) { seen[w] = 1; list.push(w); } });
  var Words = { WORDS: list };
  if (typeof module === "object" && module.exports) module.exports = Words; else root.TypeRainWords = Words;
})(typeof window !== "undefined" ? window : this);
