from PIL import Image
import os
import hashlib
import folder_paths
import torch
import numpy as np
import comfy.model_management
from comfy_extras.nodes_compositing import JoinImageWithAlpha
from server import PromptServer
from nodes import MAX_RESOLUTION
from torchvision.transforms.functional import to_pil_image
from .log import log_node_info
from .libs.image import pil2tensor, tensor2pil, ResizeMode, get_new_bounds, RGB2RGBA, image2mask
from .libs.chooser import ChooserMessage, ChooserCancelled
from .config import REMBG_DIR, REMBG_MODELS, HUMANPARSING_MODELS, MEDIAPIPE_MODELS, MEDIAPIPE_DIR


# 图像数量
class imageCount:
  @classmethod
  def INPUT_TYPES(s):
    return {
      "required": {
        "images": ("IMAGE",),
      }
    }

  CATEGORY = "EasyUse/Image"

  RETURN_TYPES = ("INT",)
  RETURN_NAMES = ("count",)
  FUNCTION = "get_count"

  def get_count(self, images):
    return (images.size(0),)

# 图像裁切
class imageInsetCrop:

  @classmethod
  def INPUT_TYPES(cls):  # pylint: disable = invalid-name, missing-function-docstring
    return {
      "required": {
        "image": ("IMAGE",),
        "measurement": (['Pixels', 'Percentage'],),
        "left": ("INT", {
          "default": 0,
          "min": 0,
          "max": MAX_RESOLUTION,
          "step": 8
        }),
        "right": ("INT", {
          "default": 0,
          "min": 0,
          "max": MAX_RESOLUTION,
          "step": 8
        }),
        "top": ("INT", {
          "default": 0,
          "min": 0,
          "max": MAX_RESOLUTION,
          "step": 8
        }),
        "bottom": ("INT", {
          "default": 0,
          "min": 0,
          "max": MAX_RESOLUTION,
          "step": 8
        }),
      },
    }

  RETURN_TYPES = ("IMAGE",)
  FUNCTION = "crop"

  CATEGORY = "EasyUse/Image"

  # pylint: disable = too-many-arguments
  def crop(self, measurement, left, right, top, bottom, image=None):
    """Does the crop."""

    _, height, width, _ = image.shape

    if measurement == 'Percentage':
      left = int(width - (width * (100 - left) / 100))
      right = int(width - (width * (100 - right) / 100))
      top = int(height - (height * (100 - top) / 100))
      bottom = int(height - (height * (100 - bottom) / 100))

    # Snap to 8 pixels
    left = left // 8 * 8
    right = right // 8 * 8
    top = top // 8 * 8
    bottom = bottom // 8 * 8

    if left == 0 and right == 0 and bottom == 0 and top == 0:
      return (image,)

    inset_left, inset_right, inset_top, inset_bottom = get_new_bounds(width, height, left, right,
                                                                      top, bottom)
    if inset_top > inset_bottom:
      raise ValueError(
        f"Invalid cropping dimensions top ({inset_top}) exceeds bottom ({inset_bottom})")
    if inset_left > inset_right:
      raise ValueError(
        f"Invalid cropping dimensions left ({inset_left}) exceeds right ({inset_right})")

    log_node_info("Image Inset Crop", f'Cropping image {width}x{height} width inset by {inset_left},{inset_right}, ' +
                 f'and height inset by {inset_top}, {inset_bottom}')
    image = image[:, inset_top:inset_bottom, inset_left:inset_right, :]

    return (image,)

# 图像尺寸
class imageSize:
  def __init__(self):
    pass

  @classmethod
  def INPUT_TYPES(cls):
    return {
      "required": {
        "image": ("IMAGE",),
      }
    }

  RETURN_TYPES = ("INT", "INT")
  RETURN_NAMES = ("width_int", "height_int")
  OUTPUT_NODE = True
  FUNCTION = "image_width_height"

  CATEGORY = "EasyUse/Image"

  def image_width_height(self, image):
    _, raw_H, raw_W, _ = image.shape

    width = raw_W
    height = raw_H

    if width is not None and height is not None:
      result = (width, height)
    else:
      result = (0, 0)
    return {"ui": {"text": "Width: "+str(width)+" , Height: "+str(height)}, "result": result}

# 图像尺寸（最长边）
class imageSizeBySide:
  def __init__(self):
    pass

  @classmethod
  def INPUT_TYPES(cls):
    return {
      "required": {
        "image": ("IMAGE",),
        "side": (["Longest", "Shortest"],)
      }
    }

  RETURN_TYPES = ("INT",)
  RETURN_NAMES = ("resolution",)
  OUTPUT_NODE = True
  FUNCTION = "image_side"

  CATEGORY = "EasyUse/Image"

  def image_side(self, image, side):
    _, raw_H, raw_W, _ = image.shape

    width = raw_W
    height = raw_H
    if width is not None and height is not None:
      if side == "Longest":
         result = (width,) if width > height else (height,)
      elif side == 'Shortest':
         result = (width,) if width < height else (height,)
    else:
      result = (0,)
    return {"ui": {"text": str(result[0])}, "result": result}

# 图像尺寸（最长边）
class imageSizeByLongerSide:
  def __init__(self):
    pass

  @classmethod
  def INPUT_TYPES(cls):
    return {
      "required": {
        "image": ("IMAGE",),
      }
    }

  RETURN_TYPES = ("INT",)
  RETURN_NAMES = ("resolution",)
  OUTPUT_NODE = True
  FUNCTION = "image_longer_side"

  CATEGORY = "EasyUse/Image"

  def image_longer_side(self, image):
    _, raw_H, raw_W, _ = image.shape

    width = raw_W
    height = raw_H
    if width is not None and height is not None:
      if width > height:
         result = (width,)
      else:
         result = (height,)
    else:
      result = (0,)
    return {"ui": {"text": str(result[0])}, "result": result}

# 图像缩放
class imageScaleDown:
  crop_methods = ["disabled", "center"]

  @classmethod
  def INPUT_TYPES(s):
    return {
      "required": {
        "images": ("IMAGE",),
        "width": (
          "INT",
          {"default": 512, "min": 1, "max": MAX_RESOLUTION, "step": 1},
        ),
        "height": (
          "INT",
          {"default": 512, "min": 1, "max": MAX_RESOLUTION, "step": 1},
        ),
        "crop": (s.crop_methods,),
      }
    }

  RETURN_TYPES = ("IMAGE",)
  CATEGORY = "EasyUse/Image"
  FUNCTION = "image_scale_down"

  def image_scale_down(self, images, width, height, crop):
    if crop == "center":
      old_width = images.shape[2]
      old_height = images.shape[1]
      old_aspect = old_width / old_height
      new_aspect = width / height
      x = 0
      y = 0
      if old_aspect > new_aspect:
        x = round((old_width - old_width * (new_aspect / old_aspect)) / 2)
      elif old_aspect < new_aspect:
        y = round((old_height - old_height * (old_aspect / new_aspect)) / 2)
      s = images[:, y: old_height - y, x: old_width - x, :]
    else:
      s = images

    results = []
    for image in s:
      img = tensor2pil(image).convert("RGB")
      img = img.resize((width, height), Image.LANCZOS)
      results.append(pil2tensor(img))

    return (torch.cat(results, dim=0),)

# 图像缩放比例
class imageScaleDownBy(imageScaleDown):
  @classmethod
  def INPUT_TYPES(s):
    return {
      "required": {
        "images": ("IMAGE",),
        "scale_by": (
          "FLOAT",
          {"default": 0.5, "min": 0.01, "max": 1.0, "step": 0.01},
        ),
      }
    }

  RETURN_TYPES = ("IMAGE",)
  CATEGORY = "EasyUse/Image"
  FUNCTION = "image_scale_down_by"

  def image_scale_down_by(self, images, scale_by):
    width = images.shape[2]
    height = images.shape[1]
    new_width = int(width * scale_by)
    new_height = int(height * scale_by)
    return self.image_scale_down(images, new_width, new_height, "center")

# 图像缩放尺寸
class imageScaleDownToSize(imageScaleDownBy):
  @classmethod
  def INPUT_TYPES(s):
    return {
      "required": {
        "images": ("IMAGE",),
        "size": ("INT", {"default": 512, "min": 1, "max": MAX_RESOLUTION, "step": 1}),
        "mode": ("BOOLEAN", {"default": True, "label_on": "max", "label_off": "min"}),
      }
    }

  RETURN_TYPES = ("IMAGE",)
  CATEGORY = "EasyUse/Image"
  FUNCTION = "image_scale_down_to_size"

  def image_scale_down_to_size(self, images, size, mode):
    width = images.shape[2]
    height = images.shape[1]

    if mode:
      scale_by = size / max(width, height)
    else:
      scale_by = size / min(width, height)

    scale_by = min(scale_by, 1.0)
    return self.image_scale_down_by(images, scale_by)


# 图像比率
class imageRatio:
  def __init__(self):
    pass

  @classmethod
  def INPUT_TYPES(cls):
    return {
      "required": {
        "image": ("IMAGE",),
      }
    }

  RETURN_TYPES = ("INT", "INT", "FLOAT", "FLOAT")
  RETURN_NAMES = ("width_ratio_int", "height_ratio_int", "width_ratio_float", "height_ratio_float")
  OUTPUT_NODE = True
  FUNCTION = "image_ratio"

  CATEGORY = "EasyUse/Image"

  def gcf(self, a, b):
    while b:
      a, b = b, a % b
    return a

  def image_ratio(self, image):
    _, raw_H, raw_W, _ = image.shape

    width = raw_W
    height = raw_H

    ratio = self.gcf(width, height)

    if width is not None and height is not None:
      width_ratio = width // ratio
      height_ratio = height // ratio
      result = (width_ratio, height_ratio, width_ratio, height_ratio)
    else:
      width_ratio = 0
      height_ratio = 0
      result = (0, 0, 0.0, 0.0)
    text = f"Image Ratio is {str(width_ratio)}:{str(height_ratio)}"

    return {"ui": {"text": text}, "result": result}


# 图像完美像素
class imagePixelPerfect:
  @classmethod
  def INPUT_TYPES(s):
    RESIZE_MODES = [ResizeMode.RESIZE.value, ResizeMode.INNER_FIT.value, ResizeMode.OUTER_FIT.value]
    return {
      "required": {
        "image": ("IMAGE",),
        "resize_mode": (RESIZE_MODES, {"default": ResizeMode.RESIZE.value})
      }
    }

  RETURN_TYPES = ("INT",)
  RETURN_NAMES = ("resolution",)
  OUTPUT_NODE = True
  FUNCTION = "execute"

  CATEGORY = "EasyUse/Image"

  def execute(self, image, resize_mode):

    _, raw_H, raw_W, _ = image.shape

    width = raw_W
    height = raw_H

    k0 = float(height) / float(raw_H)
    k1 = float(width) / float(raw_W)

    if resize_mode == ResizeMode.OUTER_FIT.value:
      estimation = min(k0, k1) * float(min(raw_H, raw_W))
    else:
      estimation = max(k0, k1) * float(min(raw_H, raw_W))

    result = int(np.round(estimation))
    text = f"Width:{str(width)}\nHeight:{str(height)}\nPixelPerfect:{str(result)}"

    return {"ui": {"text": text}, "result": (result,)}

# 图片到遮罩
class imageToMask:
  @classmethod
  def INPUT_TYPES(s):
    return {"required": {
        "image": ("IMAGE",),
        "channel": (['red', 'green', 'blue'],),
       }
    }

  RETURN_TYPES = ("MASK",)
  FUNCTION = "convert"
  CATEGORY = "EasyUse/Image"

  def convert_to_single_channel(self, image, channel='red'):
    # Convert to RGB mode to access individual channels
    image = image.convert('RGB')

    # Extract the desired channel and convert to greyscale
    if channel == 'red':
      channel_img = image.split()[0].convert('L')
    elif channel == 'green':
      channel_img = image.split()[1].convert('L')
    elif channel == 'blue':
      channel_img = image.split()[2].convert('L')
    else:
      raise ValueError(
        "Invalid channel option. Please choose 'red', 'green', or 'blue'.")

    # Convert the greyscale channel back to RGB mode
    channel_img = Image.merge(
      'RGB', (channel_img, channel_img, channel_img))

    return channel_img

  def convert(self, image, channel='red'):
    image = self.convert_to_single_channel(tensor2pil(image), channel)
    image = pil2tensor(image)
    return (image.squeeze().mean(2),)

# 图像保存 (简易)
from nodes import PreviewImage, SaveImage
class imageSaveSimple:

  def __init__(self):
    self.output_dir = folder_paths.get_output_directory()
    self.type = "output"
    self.prefix_append = ""
    self.compress_level = 4

  @classmethod
  def INPUT_TYPES(s):
    return {"required":
              {
                "images": ("IMAGE",),
                "filename_prefix": ("STRING", {"default": "ComfyUI"}),
                "only_preview": ("BOOLEAN", {"default": False}),
              },
              "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
            }

  RETURN_TYPES = ()
  FUNCTION = "save"
  OUTPUT_NODE = True
  CATEGORY = "EasyUse/Image"

  def save(self, images, filename_prefix="ComfyUI", only_preview=False, prompt=None, extra_pnginfo=None):
    if only_preview:
      PreviewImage().save_images(images, filename_prefix, prompt, extra_pnginfo)
      return ()
    else:
      return SaveImage().save_images(images, filename_prefix, prompt, extra_pnginfo)


# 图像批次合并
class JoinImageBatch:
  """Turns an image batch into one big image."""

  @classmethod
  def INPUT_TYPES(s):
    return {
      "required": {
        "images": ("IMAGE",),
        "mode": (("horizontal", "vertical"), {"default": "horizontal"}),
      },
    }

  RETURN_TYPES = ("IMAGE",)
  RETURN_NAMES = ("image",)
  FUNCTION = "join"
  CATEGORY = "EasyUse/Image"

  def join(self, images, mode):
    n, h, w, c = images.shape
    image = None
    if mode == "vertical":
      # for vertical we can just reshape
      image = images.reshape(1, n * h, w, c)
    elif mode == "horizontal":
      # for horizontal we have to swap axes
      image = torch.transpose(torch.transpose(images, 1, 2).reshape(1, n * w, h, c), 1, 2)
    return (image,)

# 图像拆分
class imageSplitList:
  @classmethod

  def INPUT_TYPES(s):
    return {
      "required": {
        "images": ("IMAGE",),
      },
    }

  RETURN_TYPES = ("IMAGE", "IMAGE", "IMAGE",)
  RETURN_NAMES = ("images", "images", "images",)
  FUNCTION = "doit"
  CATEGORY = "EasyUse/Image"

  def doit(self, images):
    length = len(images)
    new_images = ([], [], [])
    if length % 3 == 0:
      for index, img in enumerate(images):
        if index % 3 == 0:
          new_images[0].append(img)
        elif (index+1) % 3 == 0:
          new_images[2].append(img)
        else:
          new_images[1].append(img)
    elif length % 2 == 0:
      for index, img in enumerate(images):
        if index % 2 == 0:
          new_images[0].append(img)
        else:
          new_images[1].append(img)
    return new_images

class imagesSplitImage:
    @classmethod
    def INPUT_TYPES(s):
        return {
          "required": {
              "images": ("IMAGE",),
          }
        }

    RETURN_TYPES = ("IMAGE", "IMAGE", "IMAGE", "IMAGE", "IMAGE")
    RETURN_NAMES = ("image1", "image2", "image3", "image4", "image5")
    FUNCTION = "split"
    CATEGORY = "EasyUse/Image"

    def split(self, images,):
      new_images = torch.chunk(images, len(images), dim=0)
      return new_images

# 图片背景移除
from .briaai.rembg import BriaRMBG, preprocess_image, postprocess_image
from .libs.utils import get_local_filepath, easySave, install_package
class imageRemBg:
  @classmethod
  def INPUT_TYPES(self):
    return {
      "required": {
        "images": ("IMAGE",),
        "rem_mode": (("RMBG-1.4",),),
        "image_output": (["Hide", "Preview", "Save", "Hide/Save"], {"default": "Preview"}),
        "save_prefix": ("STRING", {"default": "ComfyUI"}),
      },
      "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
    }

  RETURN_TYPES = ("IMAGE", "MASK")
  RETURN_NAMES = ("image", "mask")
  FUNCTION = "remove"
  OUTPUT_NODE = True

  CATEGORY = "EasyUse/Image"

  def remove(self, rem_mode, images, image_output, save_prefix, prompt=None, extra_pnginfo=None):
    if rem_mode == "RMBG-1.4":
      # load model
      model_url = REMBG_MODELS[rem_mode]['model_url']
      suffix = model_url.split(".")[-1]
      model_path = get_local_filepath(model_url, REMBG_DIR, rem_mode+'.'+suffix)

      net = BriaRMBG()
      device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
      net.load_state_dict(torch.load(model_path, map_location=device))
      net.to(device)
      net.eval()
      # prepare input
      model_input_size = [1024, 1024]
      new_images = list()
      masks = list()
      for image in images:
        orig_im = tensor2pil(image)
        w, h = orig_im.size
        image = preprocess_image(orig_im, model_input_size).to(device)
        # inference
        result = net(image)
        result_image = postprocess_image(result[0][0], (h, w))
        mask_im = Image.fromarray(result_image)
        new_im = Image.new("RGBA", mask_im.size, (0,0,0,0))
        new_im.paste(orig_im, mask=mask_im)

        new_images.append(pil2tensor(new_im))
        masks.append(pil2tensor(mask_im))

      new_images = torch.cat(new_images, dim=0)
      masks = torch.cat(masks, dim=0)


      results = easySave(new_images, save_prefix, image_output, prompt, extra_pnginfo)

      if image_output in ("Hide", "Hide/Save"):
        return {"ui": {},
                "result": (new_images, masks)}

      return {"ui": {"images": results},
              "result": (new_images, masks)}

    else:
      return (None, None)

# 图像选择器
class imageChooser(PreviewImage):
  @classmethod
  def INPUT_TYPES(self):
    return {
      "required":{

      },
      "optional": {
        "images": ("IMAGE",),
      },
      "hidden": {"prompt": "PROMPT", "my_unique_id": "UNIQUE_ID"},
    }

  RETURN_TYPES = ("IMAGE",)
  RETURN_NAMES = ("image",)
  FUNCTION = "chooser"
  OUTPUT_NODE = True
  INPUT_IS_LIST = True
  CATEGORY = "EasyUse/Image"

  last_ic = {}
  @classmethod
  def IS_CHANGED(cls, my_unique_id, **kwargs):
    return cls.last_ic[my_unique_id[0]]

  def tensor_bundle(self, tensor_in: torch.Tensor, picks):
    if tensor_in is not None and len(picks):
      batch = tensor_in.shape[0]
      return torch.cat(tuple([tensor_in[(x) % batch].unsqueeze_(0) for x in picks])).reshape(
        [-1] + list(tensor_in.shape[1:]))
    else:
      return None

  def chooser(self, prompt=None, my_unique_id=None, **kwargs):

    id = my_unique_id[0]
    if id not in ChooserMessage.stash:
      ChooserMessage.stash[id] = {}
    my_stash = ChooserMessage.stash[id]

    # enable stashing. If images is None, we are operating in read-from-stash mode
    if 'images' in kwargs:
      my_stash['images'] = kwargs['images']
    else:
      kwargs['images'] = my_stash.get('images', None)

    if (kwargs['images'] is None):
      return (None, None, None, "")

    images_in = torch.cat(kwargs.pop('images'))
    self.batch = images_in.shape[0]
    for x in kwargs: kwargs[x] = kwargs[x][0]
    result = self.save_images(images=images_in, prompt=prompt)

    images = result['ui']['images']
    PromptServer.instance.send_sync("easyuse-image-choose", {"id": id, "urls": images})

    # wait for selection
    try:
      selections = ChooserMessage.waitForMessage(id, asList=True)
      choosen = [x for x in selections if x >= 0] if len(selections)>1 else [0]
    except ChooserCancelled:
      raise comfy.model_management.InterruptProcessingException()

    return {"ui": {"images": images},
              "result": (self.tensor_bundle(images_in, choosen),)}

class imageColorMatch(PreviewImage):
  @classmethod
  def INPUT_TYPES(cls):
    return {
      "required": {
        "image_ref": ("IMAGE",),
        "image_target": ("IMAGE",),
        "method": (['mkl', 'hm', 'reinhard', 'mvgd', 'hm-mvgd-hm', 'hm-mkl-hm'],),
        "image_output": (["Hide", "Preview", "Save", "Hide/Save"], {"default": "Preview"}),
        "save_prefix": ("STRING", {"default": "ComfyUI"}),
      },
      "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
    }

  CATEGORY = "EasyUse/Image"

  RETURN_TYPES = ("IMAGE",)
  RETURN_NAMES = ("image",)
  OUTPUT_NODE = True
  FUNCTION = "color_match"

  def color_match(self, image_ref, image_target, method, image_output, save_prefix, prompt=None, extra_pnginfo=None):
    try:
      from color_matcher import ColorMatcher
    except:
      install_package("color-matcher")
      from color_matcher import ColorMatcher
    cm = ColorMatcher()
    image_ref = image_ref.cpu()
    image_target = image_target.cpu()
    batch_size = image_target.size(0)
    out = []
    images_target = image_target.squeeze()
    images_ref = image_ref.squeeze()

    image_ref_np = images_ref.numpy()
    images_target_np = images_target.numpy()

    if image_ref.size(0) > 1 and image_ref.size(0) != batch_size:
      raise ValueError("ColorMatch: Use either single reference image or a matching batch of reference images.")

    for i in range(batch_size):
      image_target_np = images_target_np if batch_size == 1 else images_target[i].numpy()
      image_ref_np_i = image_ref_np if image_ref.size(0) == 1 else images_ref[i].numpy()
      try:
        image_result = cm.transfer(src=image_target_np, ref=image_ref_np_i, method=method)
      except BaseException as e:
        print(f"Error occurred during transfer: {e}")
        break
      out.append(torch.from_numpy(image_result))

    new_images = torch.stack(out, dim=0).to(torch.float32)

    results = easySave(new_images, save_prefix, image_output, prompt, extra_pnginfo)

    if image_output in ("Hide", "Hide/Save"):
      return {"ui": {},
              "result": (new_images,)}

    return {"ui": {"images": results},
            "result": (new_images,)}


# 图像反推
from .libs.image import ci
class imageInterrogator:
    @classmethod
    def INPUT_TYPES(self):
        return {
          "required": {
              "image": ("IMAGE",),
              "mode": (['fast','classic','best','negative'],),
              "use_lowvram": ("BOOLEAN", {"default": True}),
          }
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("prompt",)
    FUNCTION = "interrogate"
    CATEGORY = "EasyUse/Image"
    OUTPUT_NODE = True
    OUTPUT_IS_LIST = (True,)

    def interrogate(self, image, mode, use_lowvram=False):
      prompt = ci.image_to_prompt(image, mode, low_vram=use_lowvram)
      return {"ui":{"text":prompt},"result":(prompt,)}

# 人类分割器
class humanSegmentation:

    @classmethod
    def INPUT_TYPES(cls):

        return {
          "required":{
            "image": ("IMAGE",),
            "method": (["selfie_multiclass_256x256", "human_parsing_lip"],),
            "confidence": ("FLOAT", {"default": 0.4, "min": 0.05, "max": 0.95, "step": 0.01},),
          },
          "hidden": {
              "prompt": "PROMPT",
              "my_unique_id": "UNIQUE_ID",
          }
        }

    RETURN_TYPES = ("IMAGE", "MASK",)
    RETURN_NAMES = ("image", "mask",)
    FUNCTION = "parsing"
    CATEGORY = "EasyUse/Segmentation"

    def get_mediapipe_image(self, image: Image):
      import mediapipe as mp
      # Convert image to NumPy array
      numpy_image = np.asarray(image)
      image_format = mp.ImageFormat.SRGB
      # Convert BGR to RGB (if necessary)
      if numpy_image.shape[-1] == 4:
        image_format = mp.ImageFormat.SRGBA
      elif numpy_image.shape[-1] == 3:
        image_format = mp.ImageFormat.SRGB
        numpy_image = cv2.cvtColor(numpy_image, cv2.COLOR_BGR2RGB)
      return mp.Image(image_format=image_format, data=numpy_image)

    def parsing(self, image, confidence, method, prompt=None, my_unique_id=None):
      mask_components = []
      if my_unique_id in prompt:
        if prompt[my_unique_id]["inputs"]['mask_components']:
          mask_components = prompt[my_unique_id]["inputs"]['mask_components'].split(',')
      mask_components = list(map(int, mask_components))
      if method == 'selfie_multiclass_256x256':
        try:
          import mediapipe as mp
        except:
          install_package("mediapipe")
          import mediapipe as mp

        from functools import reduce

        model_path = get_local_filepath(MEDIAPIPE_MODELS['selfie_multiclass_256x256']['model_url'], MEDIAPIPE_DIR)
        model_asset_buffer = None
        with open(model_path, "rb") as f:
            model_asset_buffer = f.read()
        image_segmenter_base_options = mp.tasks.BaseOptions(model_asset_buffer=model_asset_buffer)
        options = mp.tasks.vision.ImageSegmenterOptions(
          base_options=image_segmenter_base_options,
          running_mode=mp.tasks.vision.RunningMode.IMAGE,
          output_category_mask=True)
        # Create the image segmenter
        ret_images = []
        ret_masks = []

        with mp.tasks.vision.ImageSegmenter.create_from_options(options) as segmenter:
            for img in image:
                _image = torch.unsqueeze(img, 0)
                orig_image = tensor2pil(_image).convert('RGB')
                # Convert the Tensor to a PIL image
                i = 255. * img.cpu().numpy()
                image_pil = Image.fromarray(np.clip(i, 0, 255).astype(np.uint8))
                # create our foreground and background arrays for storing the mask results
                mask_background_array = np.zeros((image_pil.size[0], image_pil.size[1], 4), dtype=np.uint8)
                mask_background_array[:] = (0, 0, 0, 255)
                mask_foreground_array = np.zeros((image_pil.size[0], image_pil.size[1], 4), dtype=np.uint8)
                mask_foreground_array[:] = (255, 255, 255, 255)
                # Retrieve the masks for the segmented image
                media_pipe_image = self.get_mediapipe_image(image=image_pil)
                segmented_masks = segmenter.segment(media_pipe_image)
                masks = []
                for i, com in enumerate(mask_components):
                    masks.append(segmented_masks.confidence_masks[com])

                image_data = media_pipe_image.numpy_view()
                image_shape = image_data.shape
                # convert the image shape from "rgb" to "rgba" aka add the alpha channel
                if image_shape[-1] == 3:
                    image_shape = (image_shape[0], image_shape[1], 4)
                mask_background_array = np.zeros(image_shape, dtype=np.uint8)
                mask_background_array[:] = (0, 0, 0, 255)
                mask_foreground_array = np.zeros(image_shape, dtype=np.uint8)
                mask_foreground_array[:] = (255, 255, 255, 255)
                mask_arrays = []
                if len(masks) == 0:
                    mask_arrays.append(mask_background_array)
                else:
                    for i, mask in enumerate(masks):
                        condition = np.stack((mask.numpy_view(),) * image_shape[-1], axis=-1) > confidence
                        mask_array = np.where(condition, mask_foreground_array, mask_background_array)
                        mask_arrays.append(mask_array)
                # Merge our masks taking the maximum from each
                merged_mask_arrays = reduce(np.maximum, mask_arrays)
                # Create the image
                mask_image = Image.fromarray(merged_mask_arrays)
                # convert PIL image to tensor image
                tensor_mask = mask_image.convert("RGB")
                tensor_mask = np.array(tensor_mask).astype(np.float32) / 255.0
                tensor_mask = torch.from_numpy(tensor_mask)[None,]
                _mask = tensor_mask.squeeze(3)[..., 0]

                _mask = tensor2pil(tensor_mask).convert('L')

                ret_image = RGB2RGBA(orig_image, _mask)
                ret_images.append(pil2tensor(ret_image))
                ret_masks.append(image2mask(_mask))

            output_image = torch.cat(ret_images, dim=0)
            mask = torch.cat(ret_masks, dim=0)

      elif method == "human_parsing_lip":
        from .human_parsing.run_parsing import HumanParsing
        onnx_path = os.path.join(folder_paths.models_dir, 'onnx')
        model_path = get_local_filepath(HUMANPARSING_MODELS['parsing_lip']['model_url'], onnx_path)
        parsing = HumanParsing(model_path=model_path)
        model_image = image.squeeze(0)
        model_image = model_image.permute((2, 0, 1))
        model_image = to_pil_image(model_image)

        map_image, mask = parsing(model_image, mask_components)

        mask = mask[:, :, :, 0]

        alpha = 1.0 - mask

        output_image, = JoinImageWithAlpha().join_image_with_alpha(image, alpha)

      return (output_image, mask)

import cv2
import base64
class loadImageBase64:
  @classmethod
  def INPUT_TYPES(s):
    return {
      "required": {
        "base64_data": ("STRING", {"default": ""}),
        "image_output": (["Hide", "Preview", "Save", "Hide/Save"], {"default": "Preview"}),
        "save_prefix": ("STRING", {"default": "ComfyUI"}),
      },
      "optional": {

      },
      "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO"},
    }

  RETURN_TYPES = ("IMAGE", "MASK")
  OUTPUT_NODE = True
  FUNCTION = "load_image"
  CATEGORY = "EasyUse/Image/LoadImage"

  def convert_color(self, image,):
    if len(image.shape) > 2 and image.shape[2] >= 4:
      return cv2.cvtColor(image, cv2.COLOR_BGRA2RGB)
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

  def load_image(self, base64_data, image_output, save_prefix, prompt=None, extra_pnginfo=None):
    nparr = np.frombuffer(base64.b64decode(base64_data), np.uint8)

    result = cv2.imdecode(nparr, cv2.IMREAD_UNCHANGED)
    channels = cv2.split(result)
    if len(channels) > 3:
      mask = channels[3].astype(np.float32) / 255.0
      mask = torch.from_numpy(mask)
    else:
      mask = torch.ones(channels[0].shape, dtype=torch.float32, device="cpu")

    result = self.convert_color(result)
    result = result.astype(np.float32) / 255.0
    new_images = torch.from_numpy(result)[None,]

    results = easySave(new_images, save_prefix, image_output, None, None)
    mask = mask.unsqueeze(0)

    if image_output in ("Hide", "Hide/Save"):
      return {"ui": {},
              "result": (new_images, mask)}

    return {"ui": {"images": results},
            "result": (new_images, mask)}

class imageToBase64:
    @classmethod
    def INPUT_TYPES(s):
        return {
        "required": {
            "image": ("IMAGE",),
        },
        }

    RETURN_TYPES = ("STRING",)
    FUNCTION = "to_base64"
    CATEGORY = "EasyUse/Image"
    OUTPUT_NODE = True

    def to_base64(self, image, ):
      import base64
      from io import BytesIO

      # 将张量图像转换为PIL图像
      pil_image = tensor2pil(image)

      buffered = BytesIO()
      pil_image.save(buffered, format="JPEG")
      image_bytes = buffered.getvalue()

      base64_str = base64.b64encode(image_bytes).decode("utf-8")
      return {"result": (base64_str,)}

# 姿势编辑器
class poseEditor:
  @classmethod
  def INPUT_TYPES(self):
    temp_dir = folder_paths.get_temp_directory()

    if not os.path.isdir(temp_dir):
      os.makedirs(temp_dir)

    temp_dir = folder_paths.get_temp_directory()

    return {"required":
              {"image": (sorted(os.listdir(temp_dir)),)},
            }

  RETURN_TYPES = ("IMAGE",)
  FUNCTION = "output_pose"

  CATEGORY = "EasyUse/Image"

  def output_pose(self, image):
    image_path = os.path.join(folder_paths.get_temp_directory(), image)
    # print(f"Create: {image_path}")

    i = Image.open(image_path)
    image = i.convert("RGB")
    image = np.array(image).astype(np.float32) / 255.0
    image = torch.from_numpy(image)[None,]

    return (image,)

  @classmethod
  def IS_CHANGED(self, image):
    image_path = os.path.join(
      folder_paths.get_temp_directory(), image)
    # print(f'Change: {image_path}')

    m = hashlib.sha256()
    with open(image_path, 'rb') as f:
      m.update(f.read())
    return m.digest().hex()

NODE_CLASS_MAPPINGS = {
  "easy imageInsetCrop": imageInsetCrop,
  "easy imageCount": imageCount,
  "easy imageSize": imageSize,
  "easy imageSizeBySide": imageSizeBySide,
  "easy imageSizeByLongerSide": imageSizeByLongerSide,
  "easy imagePixelPerfect": imagePixelPerfect,
  "easy imageScaleDown": imageScaleDown,
  "easy imageScaleDownBy": imageScaleDownBy,
  "easy imageScaleDownToSize": imageScaleDownToSize,
  "easy imageRatio": imageRatio,
  "easy imageToMask": imageToMask,
  "easy imageSplitList": imageSplitList,
  "easy imagesSplitImage": imagesSplitImage,
  "easy imageSave": imageSaveSimple,
  "easy imageRemBg": imageRemBg,
  "easy imageChooser": imageChooser,
  "easy imageColorMatch": imageColorMatch,
  "easy imageInterrogator": imageInterrogator,
  "easy joinImageBatch": JoinImageBatch,
  "easy loadImageBase64": loadImageBase64,
  "easy imageToBase64": imageToBase64,
  "easy humanSegmentation": humanSegmentation,
  "easy poseEditor": poseEditor
}

NODE_DISPLAY_NAME_MAPPINGS = {
  "easy imageInsetCrop": "ImageInsetCrop",
  "easy imageCount": "ImageCount",
  "easy imageSize": "ImageSize",
  "easy imageSizeBySide": "ImageSize (Side)",
  "easy imageSizeByLongerSide": "ImageSize (LongerSide)",
  "easy imagePixelPerfect": "ImagePixelPerfect",
  "easy imageScaleDown": "Image Scale Down",
  "easy imageScaleDownBy": "Image Scale Down By",
  "easy imageScaleDownToSize": "Image Scale Down To Size",
  "easy imageRatio": "ImageRatio",
  "easy imageToMask": "ImageToMask",
  "easy imageHSVMask": "ImageHSVMask",
  "easy imageSplitList": "imageSplitList",
  "easy imagesSplitImage": "imagesSplitImage",
  "easy imageSave": "SaveImage (Simple)",
  "easy imageRemBg": "Image Remove Bg",
  "easy imageChooser": "Image Chooser",
  "easy imageColorMatch": "Image Color Match",
  "easy imageInterrogator": "Image To Prompt",
  "easy joinImageBatch": "JoinImageBatch",
  "easy loadImageBase64": "Load Image (Base64)",
  "easy imageToBase64": "Image To Base64",
  "easy humanSegmentation": "Human Segmentation",
  "easy poseEditor": "PoseEditor",
}