#!/usr/bin/env python3
"""
Multi Particle Texture Combiner

Combine 2 textures : one as mask the other as texture.

The result is a pre-baked texture atlas that achieves the WoD+ multitexture
look without needing Cata+ shader support.

Usage:
    python3 Multi_Particle_Texture_Combiner.py <mask.blp/png> <color.blp/png> <output.png> [options]
    python3 Multi_Particle_Texture_Combiner.py <mask.blp/png> <color.blp/png> <output.png> --color2 <color2> [options]
    
Options:
    --grid N            Output atlas grid NxN (default: 8, giving 64 frames)
    --cell-size N       Size of each cell in pixels (default: 256)
    --mask-grid N       If mask is 1x1 or 2x2 etc pattern (default 0, try to autodetect)
    --mode MODE         Compositing mode: multiply, screen, overlay (default: multiply)
    --variation         Add random variation to color sampling per frame, else use --no-variation
    --preview           Also generate a preview strip
    --seed N            Use a seed number to replicate same output texture, else it is random
    --color2 <color2>   experimental, trying to blend 2 "colors" together suggested to try different blending mode with --blend12
    --blend12 <mode>    mode being multiply, add, overlay, screen, if not specified, multiply is used by default

Unrecommended to generate texture higher than 1024 pixel per side, so use --cell-size to avoid that
Example:
    
    py Multi_Particle_Texture_Combiner.py fire_2x2_sharp_mod4x.png fire_bright_mod2x_a.png test2.png --mask-grid 2 --grid 8 --cell-size 128 --seed 60

Requirements:
    pip install Pillow
"""

import argparse
import os
import sys
import random
from pathlib import Path

try:
    from PIL import Image, ImageEnhance, ImageFilter, ImageChops
    HAS_PIL = True
except ImportError:
    print("ERROR: Pillow is required. Install with:")
    print("  pip install Pillow --break-system-packages")
    sys.exit(1)


def detect_grid_size(image):
    """Detect if image is an atlas and return grid dimensions"""
    w, h = image.size
    
    if w != h:
        return 1, 1  # Not square, assume single image
    
    # Common atlas sizes
    for grid in [8, 4, 2]:
        cell_size = w // grid
        if cell_size >= 32 and w % grid == 0:
            return grid, grid
    
    return 1, 1


def extract_cells(image, grid_rows, grid_cols):
    """Extract individual cells from an atlas image"""
    w, h = image.size
    cell_w = w // grid_cols
    cell_h = h // grid_rows
    
    cells = []
    for row in range(grid_rows):
        for col in range(grid_cols):
            cell = image.crop((
                col * cell_w,
                row * cell_h,
                (col + 1) * cell_w,
                (row + 1) * cell_h
            ))
            cells.append(cell)
    
    return cells, cell_w, cell_h


def sample_color_with_offset(color_img, offset_x, offset_y, size):
    """Sample a region from color image with offset, tiling as needed"""
    w, h = color_img.size
    result = Image.new('RGBA', size)
    
    for y in range(size[1]):
        for x in range(size[0]):
            src_x = (x + offset_x) % w
            src_y = (y + offset_y) % h
            result.putpixel((x, y), color_img.getpixel((src_x, src_y)))
    
    return result


def blend_multiply(base, top):
    """Multiply blend: base * top / 255"""
    return ImageChops.multiply(base, top)


def blend_screen(base, top):
    """Screen blend: 255 - ((255-base) * (255-top) / 255)"""
    return ImageChops.screen(base, top)


def blend_add(base, top):
    """Additive blend: base + top (clamped)"""
    return ImageChops.add(base, top)


def blend_overlay(base, top):
    """Overlay blend"""
    result = Image.new('RGBA', base.size)
    base_data = base.load()
    top_data = top.load()
    result_data = result.load()
    
    for y in range(base.size[1]):
        for x in range(base.size[0]):
            b = base_data[x, y]
            t = top_data[x, y]
            
            out = []
            for i in range(3):
                if b[i] < 128:
                    out.append(int(2 * b[i] * t[i] / 255))
                else:
                    out.append(int(255 - 2 * (255 - b[i]) * (255 - t[i]) / 255))
            
            a = max(b[3] if len(b) > 3 else 255, t[3] if len(t) > 3 else 255)
            result_data[x, y] = (out[0], out[1], out[2], a)
    
    return result


def composite_2tex(mask, color, mode='multiply'):
    """
    Composite mask with single color texture.
    - Mask luminance controls alpha/visibility
    - Color provides RGB values (unchanged)
    """
    if mask.size != color.size:
        color = color.resize(mask.size, Image.LANCZOS)
    
    result = Image.new('RGBA', mask.size)
    mask_data = mask.load()
    color_data = color.load()
    result_data = result.load()
    
    for y in range(mask.size[1]):
        for x in range(mask.size[0]):
            m = mask_data[x, y]
            c = color_data[x, y]
            
            # Get mask luminance
            if len(m) >= 3:
                m_luminance = (m[0] + m[1] + m[2]) / (3.0 * 255.0)
            else:
                m_luminance = m[0] / 255.0
            
            # Apply mask alpha if present
            if len(m) == 4:
                m_alpha = m[3] / 255.0
            else:
                m_alpha = 1.0
            
            # Output alpha from mask luminance * mask alpha
            out_alpha = m_luminance * m_alpha
            
            # RGB comes from color texture (unchanged, not darkened)
            r = c[0]
            g = c[1]
            b = c[2]
            a = int(255 * out_alpha)
            
            result_data[x, y] = (r, g, b, a)
    
    return result
    
    return result


def composite_3tex(mask, color1, color2, mode='multiply', blend12='multiply'):
    """
    Composite mask with two color textures (3-texture multitexturing)
    - Mask luminance controls alpha/visibility
    - Color1 and Color2 are blended together for RGB
    """
    if mask.size != color1.size:
        color1 = color1.resize(mask.size, Image.LANCZOS)
    if mask.size != color2.size:
        color2 = color2.resize(mask.size, Image.LANCZOS)
    
    result = Image.new('RGBA', mask.size)
    mask_data = mask.load()
    color1_data = color1.load()
    color2_data = color2.load()
    result_data = result.load()
    
    for y in range(mask.size[1]):
        for x in range(mask.size[0]):
            m = mask_data[x, y]
            c1 = color1_data[x, y]
            c2 = color2_data[x, y]
            
            # Get mask luminance
            if len(m) >= 3:
                m_luminance = (m[0] + m[1] + m[2]) / (3.0 * 255.0)
            else:
                m_luminance = m[0] / 255.0
            
            if len(m) == 4:
                m_alpha = m[3] / 255.0
            else:
                m_alpha = 1.0
            
            # Output alpha from mask
            out_alpha = m_luminance * m_alpha
            
            # Blend color1 and color2 together for RGB
            if blend12 == 'multiply':
                r = int(c1[0] * c2[0] / 255)
                g = int(c1[1] * c2[1] / 255)
                b = int(c1[2] * c2[2] / 255)
            elif blend12 == 'add':
                r = min(255, c1[0] + c2[0])
                g = min(255, c1[1] + c2[1])
                b = min(255, c1[2] + c2[2])
            elif blend12 == 'screen':
                r = int(255 - ((255 - c1[0]) * (255 - c2[0]) / 255))
                g = int(255 - ((255 - c1[1]) * (255 - c2[1]) / 255))
                b = int(255 - ((255 - c1[2]) * (255 - c2[2]) / 255))
            elif blend12 == 'overlay':
                r = int(2 * c1[0] * c2[0] / 255) if c1[0] < 128 else int(255 - 2 * (255 - c1[0]) * (255 - c2[0]) / 255)
                g = int(2 * c1[1] * c2[1] / 255) if c1[1] < 128 else int(255 - 2 * (255 - c1[1]) * (255 - c2[1]) / 255)
                b = int(2 * c1[2] * c2[2] / 255) if c1[2] < 128 else int(255 - 2 * (255 - c1[2]) * (255 - c2[2]) / 255)
            else:
                r = int(c1[0] * c2[0] / 255)
                g = int(c1[1] * c2[1] / 255)
                b = int(c1[2] * c2[2] / 255)
            
            a = int(255 * out_alpha)
            result_data[x, y] = (min(255, max(0, r)), min(255, max(0, g)), min(255, max(0, b)), a)
    
    return result


def create_composite_atlas(mask_path, color1_path, output_path, 
                           color2_path=None,
                           grid_size=8, cell_size=256, 
                           mode='multiply', blend12='multiply',
                           add_variation=True, mask_grid_override=None):
    """
    Create a composite texture atlas from mask and color textures
    """
    print(f"Loading mask: {mask_path}")
    mask_img = Image.open(mask_path).convert('RGBA')
    
    print(f"Loading color1: {color1_path}")
    color1_img = Image.open(color1_path).convert('RGBA')
    
    color2_img = None
    if color2_path:
        print(f"Loading color2: {color2_path}")
        color2_img = Image.open(color2_path).convert('RGBA')
        print(f"Mode: 3-texture (mask + color1 + color2)")
    else:
        print(f"Mode: 2-texture (mask + color)")
    
    print(f"Mask size: {mask_img.size}")
    print(f"Color1 size: {color1_img.size}")
    if color2_img:
        print(f"Color2 size: {color2_img.size}")
    
    # Detect or use override for mask atlas grid
    if mask_grid_override:
        mask_grid_rows, mask_grid_cols = mask_grid_override, mask_grid_override
        print(f"Using mask grid override: {mask_grid_rows}x{mask_grid_cols}")
    else:
        mask_grid_rows, mask_grid_cols = detect_grid_size(mask_img)
        print(f"Detected mask grid: {mask_grid_rows}x{mask_grid_cols}")
    
    # Extract mask cells
    mask_cells, mask_cell_w, mask_cell_h = extract_cells(
        mask_img, mask_grid_rows, mask_grid_cols
    )
    print(f"Extracted {len(mask_cells)} mask cells ({mask_cell_w}x{mask_cell_h})")
    
    # Create output atlas
    output_size = cell_size * grid_size
    output = Image.new('RGBA', (output_size, output_size), (0, 0, 0, 0))
    
    total_frames = grid_size * grid_size
    
    print(f"Creating {total_frames} composited frames ({cell_size}x{cell_size} each)...")
    
    for frame_idx in range(total_frames):
        # Get mask cell (cycle through available)
        mask_cell = mask_cells[frame_idx % len(mask_cells)]
        
        # Resize mask cell to target size
        if mask_cell.size != (cell_size, cell_size):
            mask_cell = mask_cell.resize((cell_size, cell_size), Image.LANCZOS)
        
        # Get color1 sample
        if add_variation:
            offset_x = random.randint(0, color1_img.size[0] - 1)
            offset_y = random.randint(0, color1_img.size[1] - 1)
            color1_sample = sample_color_with_offset(
                color1_img, offset_x, offset_y, (cell_size, cell_size)
            )
        else:
            color1_sample = color1_img.resize((cell_size, cell_size), Image.LANCZOS)
        
        # Composite
        if color2_img:
            # 3-texture mode
            if add_variation:
                offset_x2 = random.randint(0, color2_img.size[0] - 1)
                offset_y2 = random.randint(0, color2_img.size[1] - 1)
                color2_sample = sample_color_with_offset(
                    color2_img, offset_x2, offset_y2, (cell_size, cell_size)
                )
            else:
                color2_sample = color2_img.resize((cell_size, cell_size), Image.LANCZOS)
            
            composited = composite_3tex(mask_cell, color1_sample, color2_sample, mode, blend12)
        else:
            # 2-texture mode
            composited = composite_2tex(mask_cell, color1_sample, mode)
        
        # Place in output atlas
        out_row = frame_idx // grid_size
        out_col = frame_idx % grid_size
        output.paste(composited, (out_col * cell_size, out_row * cell_size))
    
    # Save output
    output.save(output_path)
    print(f"Saved: {output_path}")
    print(f"Output size: {output_size}x{output_size}")
    
    return output


def create_preview(atlas, output_path, frames_to_show=8):
    """Create a horizontal strip preview"""
    cell_size = atlas.size[0] // 8
    
    preview_h = cell_size
    preview_w = cell_size * frames_to_show
    preview = Image.new('RGBA', (preview_w, preview_h), (32, 32, 32, 255))
    
    for i in range(frames_to_show):
        row = i // 8
        col = i % 8
        cell = atlas.crop((
            col * cell_size,
            row * cell_size,
            (col + 1) * cell_size,
            (row + 1) * cell_size
        ))
        preview.paste(cell, (i * cell_size, 0), cell)
    
    preview.save(output_path)
    print(f"Preview saved: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Create WotLK-compatible fire particle textures',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Basic usage
    python3 Multi_Particle_Texture_Combiner.py <mask.blp/png> <color.blp/png> <output.png> [options]
    python3 Multi_Particle_Texture_Combiner.py <mask.blp/png> <color.blp/png> <output.png> --color2 <color2> [options]
"""
    )
    
    parser.add_argument('mask', help='Path to mask/smoke texture')
    parser.add_argument('color', help='Path to color1 texture')  
    parser.add_argument('output', help='Output path for composite atlas')
    parser.add_argument('--color2', type=str, default=None,
                        help='Path to color2 texture (for 3-texture mode)')
    parser.add_argument('--grid', type=int, default=8, help='Output grid size NxN (default: 8)')
    parser.add_argument('--cell-size', type=int, default=256, help='Cell size in pixels (default: 256)')
    parser.add_argument('--mask-grid', type=int, default=0, help='Mask grid size (0=auto-detect)')
    parser.add_argument('--mode', choices=['multiply', 'screen', 'overlay', 'add'], default='multiply',
                        help='Blend mode for mask (default: multiply)')
    parser.add_argument('--blend12', choices=['multiply', 'screen', 'overlay', 'add'], default='multiply',
                        help='Blend mode between color1 and color2 (default: multiply)')
    parser.add_argument('--no-variation', action='store_true', help='Disable random color sampling')
    parser.add_argument('--preview', action='store_true', help='Generate preview strip')
    parser.add_argument('--seed', type=int, help='Random seed for reproducible results')
    
    args = parser.parse_args()
    
    if args.seed is not None:
        random.seed(args.seed)
    
    print("=" * 60)
    print("Multi Particle Texture Combiner")
    print("=" * 60)
    
    atlas = create_composite_atlas(
        args.mask,
        args.color,
        args.output,
        color2_path=args.color2,
        grid_size=args.grid,
        cell_size=args.cell_size,
        mode=args.mode,
        blend12=args.blend12,
        add_variation=not args.no_variation,
        mask_grid_override=args.mask_grid if args.mask_grid > 0 else None
    )
    
    if args.preview:
        preview_path = str(Path(args.output).stem) + '_preview.png'
        create_preview(atlas, preview_path)
    
    print("\nDone! You can now:")
    print(f"  1. Convert {args.output} to BLP format")
    print(f"  2. Update your .M2 particle to use this texture")
    print(f"     - Check that texture index point to that specific baked texture")
    print(f"     - Set GeometryModel_Length to 1")
    print(f"     - .m2 EOL add a 4 bytes empty chunk")
    print(f"     - Set GeometryModel_Offset to the start of that previous chunk added")
    print(f"     - Set RecursionModel_Length to 1")
    print(f"     - .m2 EOL add a new 4 bytes empty chunk")
    print(f"     - Set RecursionModel_Offset to the start of the second chunk added")
    print(f"     - Set Texture_dimensions_rows and Texture_dimensions_columns to new value: {args.grid}x{args.grid}")
    print(f"     - Check particle_Flag and blending used")
    print(f"  3. repeat for other particules using multi-textures, you can reuse the same chunk added if no extra setting")


if __name__ == '__main__':
    main()