/** Rover photos and 3D models: one place for URLs, credits and alt texts, keyed by rover id. */

import curiosityImg from "../assets/rovers/curiosity.webp";
import ingenuityImg from "../assets/rovers/ingenuity.webp";
import opportunityImg from "../assets/rovers/opportunity.webp";
import perseveranceImg from "../assets/rovers/perseverance.webp";
import spiritImg from "../assets/rovers/spirit.webp";

const CDN = "https://assets.science.nasa.gov/content/dam/science/psd/mars/resources/gltf_files/";

export interface RoverPhoto {
  src: string;
  /** Real size of the file, used for the width/height attributes. */
  width: number;
  height: number;
  alt: string;
  /** Exact credit line shown next to the photo. */
  credit: string;
  /** Photojournal ID and page of the source image. */
  source: string;
  sourceUrl: string;
}

export interface RoverModel {
  src: string;
  /** Size in MB, shown on the button. */
  sizeMb: number;
  /** Human-readable name of the model (twins share one). */
  label: string;
  resourceUrl: string;
}

export const ROVER_PHOTOS: Record<string, RoverPhoto> = {
  curiosity: {
    src: curiosityImg,
    width: 720,
    height: 475,
    alt: "Curiosity on Mars, self-portrait at Mont Mercou",
    credit: "Curiosity self-portrait, sol 3070. NASA/JPL-Caltech/MSSS",
    source: "PIA24543",
    sourceUrl: "https://science.nasa.gov/photojournal/curiositys-selfie-at-mont-mercou/",
  },
  perseverance: {
    src: perseveranceImg,
    width: 720,
    height: 529,
    alt: "Perseverance on Mars, self-portrait with the Ingenuity helicopter",
    credit: "Perseverance with Ingenuity, sol 46. NASA/JPL-Caltech/MSSS",
    source: "PIA24542",
    sourceUrl: "https://science.nasa.gov/photojournal/perseverances-selfie-with-ingenuity/",
  },
  ingenuity: {
    src: ingenuityImg,
    width: 720,
    height: 535,
    alt: "Ingenuity helicopter on the surface of Mars, seen by Mastcam-Z",
    credit: "Ingenuity seen by Mastcam-Z, sol 45. NASA/JPL-Caltech/ASU",
    source: "PIA24547",
    sourceUrl: "https://science.nasa.gov/photojournal/mastcam-z-gives-ingenuity-a-close-up/",
  },
  spirit: {
    src: spiritImg,
    width: 720,
    height: 656,
    alt: "Spirit on Mars, self-portrait",
    credit: "Spirit self-portrait, sols 329–330. NASA/JPL/Cornell",
    source: "PIA07371",
    sourceUrl: "https://science.nasa.gov/photojournal/spirit-self-portrait-sols-329-330/",
  },
  opportunity: {
    src: opportunityImg,
    width: 720,
    height: 563,
    alt: "Opportunity on Mars, self-portrait after its solar panels were cleaned",
    credit:
      "Opportunity self-portrait, March 2014. NASA/JPL-Caltech/Cornell Univ./Arizona State Univ.",
    source: "PIA18079",
    sourceUrl:
      "https://science.nasa.gov/photojournal/self-portrait-by-freshly-cleaned-opportunity-mars-rover-in-march-2014/",
  },
};

const MER: RoverModel = {
  src: `${CDN}24883_MER_static.glb`,
  sizeMb: 12,
  label: "Mars Exploration Rover model (Spirit and Opportunity are twins)",
  resourceUrl: "https://science.nasa.gov/resource/spirit-and-opportunity-rover-3d-model/",
};

export const ROVER_MODELS: Record<string, RoverModel> = {
  curiosity: {
    src: `${CDN}24584_Curiosity_static.glb`,
    sizeMb: 12,
    label: "Curiosity rover model",
    resourceUrl: "https://science.nasa.gov/resource/curiosity-rover-3d-model/",
  },
  perseverance: {
    src: `${CDN}25042_Perseverance.glb`,
    sizeMb: 12,
    label: "Perseverance rover model",
    resourceUrl: "https://science.nasa.gov/resource/mars-perseverance-rover-3d-model/",
  },
  ingenuity: {
    src: `${CDN}25043_Ingenuity_v3.glb`,
    sizeMb: 2,
    label: "Ingenuity helicopter model",
    resourceUrl: "https://science.nasa.gov/resource/mars-ingenuity-helicopter-3d-model/",
  },
  spirit: MER,
  opportunity: MER,
};
