/* CameraPreview.vala - Modern GTK4 Implementation */

using Gtk;
using Gdk;
using GLib;
using Cairo;

namespace Sentinel {

    public class CameraPreview : Gtk.Box {
        private Gtk.Overlay overlay;
        private Gtk.Picture picture;
        private Gtk.DrawingArea drawing_area;
        private int[]? face_box;
        private string status_text = "";
        private double confidence = 0.0;
        private Gdk.RGBA box_color;

        // Track actual frame size for accurate box scaling
        private int frame_width = 640;
        private int frame_height = 480;

        public CameraPreview () {
            this.orientation = Gtk.Orientation.VERTICAL;
            this.vexpand = true;
            this.hexpand = true;

            overlay = new Gtk.Overlay ();
            overlay.vexpand = true;
            overlay.hexpand = true;

            // 1. Picture widget for the hardware-accelerated video feed
            picture = new Gtk.Picture ();
            picture.content_fit = Gtk.ContentFit.CONTAIN;
            picture.vexpand = true;
            picture.hexpand = true;
            overlay.set_child (picture);

            // 2. DrawingArea overlay for Cairo bounding boxes
            drawing_area = new Gtk.DrawingArea ();
            drawing_area.set_draw_func (draw_function);
            overlay.add_overlay (drawing_area);

            this.append (overlay);

            // Default box color (yellow)
            box_color = Gdk.RGBA ();
            box_color.parse ("#FFD700");
        }

        public void set_frame_from_base64 (string base64_data) {
            try {
                // Decode base64
                uint8[] image_data = Base64.decode (base64_data);

                // Load into Pixbuf, then directly to a GPU Texture
                var stream = new MemoryInputStream.from_data (image_data);
                var pixbuf = new Gdk.Pixbuf.from_stream (stream);

                // Update real dimensions for bounding box math
                frame_width = pixbuf.get_width ();
                frame_height = pixbuf.get_height ();

                // Set hardware-accelerated texture
                picture.set_paintable (Gdk.Texture.for_pixbuf (pixbuf));

                // Trigger bounding box redraw
                drawing_area.queue_draw ();
            } catch (Error e) {
                warning ("Failed to load frame: %s", e.message);
            }
        }

        public void set_face_box (int x, int y, int width, int height) {
            face_box = { x, y, width, height };
            drawing_area.queue_draw ();
        }

        public void clear_face_box () {
            face_box = null;
            drawing_area.queue_draw ();
        }

        public void clear_frame () {
            picture.set_paintable (null);
            drawing_area.queue_draw ();
        }

        public void set_status_text (string text) {
            status_text = text;
            drawing_area.queue_draw ();
        }

        public void set_confidence (double conf) {
            confidence = conf;
            drawing_area.queue_draw ();
        }

        public void set_box_color_by_status (string status) {
            box_color = Gdk.RGBA ();
            switch (status) {
                case "SUCCESS" :
                case "RECOGNIZED" : box_color.parse ("#00FF00"); break; // Green
                case "FAILURE": box_color.parse ("#FF0000"); break;     // Red
                case "REQUIRE_2FA": box_color.parse ("#FFA500"); break; // Orange
                default: box_color.parse ("#FFD700"); break;            // Yellow
            }
            drawing_area.queue_draw ();
        }

        public void set_box_color_from_string (string color_hex) {
            box_color = Gdk.RGBA ();
            box_color.parse (color_hex);
            drawing_area.queue_draw ();
        }

        private void draw_function (Gtk.DrawingArea da, Cairo.Context cr, int width, int height) {
            // Note: We don't paint the camera frame here! Gtk.Picture handles it below us.

            // Calculate scaling to match Gtk.Picture's exact CONTAIN behavior
            double scale_x = (double) width / frame_width;
            double scale_y = (double) height / frame_height;
            double scale = double.min (scale_x, scale_y);

            int scaled_width = (int) (frame_width * scale);
            int scaled_height = (int) (frame_height * scale);

            int offset_x = (width - scaled_width) / 2;
            int offset_y = (height - scaled_height) / 2;

            // Draw face box if present
            if (face_box != null && face_box.length == 4) {
                int box_x = (int) (face_box[0] * scale) + offset_x;
                int box_y = (int) (face_box[1] * scale) + offset_y;
                int box_w = (int) (face_box[2] * scale);
                int box_h = (int) (face_box[3] * scale);

                cr.set_source_rgba (box_color.red, box_color.green, box_color.blue, 0.8);
                cr.set_line_width (3.0);
                cr.rectangle (box_x, box_y, box_w, box_h);
                cr.stroke ();

                // Draw corner accents
                int corner_len = 20;
                cr.set_line_width (4.0);

                cr.move_to (box_x, box_y + corner_len); cr.line_to (box_x, box_y); cr.line_to (box_x + corner_len, box_y); cr.stroke ();
                cr.move_to (box_x + box_w - corner_len, box_y); cr.line_to (box_x + box_w, box_y); cr.line_to (box_x + box_w, box_y + corner_len); cr.stroke ();
                cr.move_to (box_x, box_y + box_h - corner_len); cr.line_to (box_x, box_y + box_h); cr.line_to (box_x + corner_len, box_y + box_h); cr.stroke ();
                cr.move_to (box_x + box_w - corner_len, box_y + box_h); cr.line_to (box_x + box_w, box_y + box_h); cr.line_to (box_x + box_w, box_y + box_h - corner_len); cr.stroke ();

                // Draw confidence percentage
                if (confidence > 0.0) {
                    cr.select_font_face ("Sans", Cairo.FontSlant.NORMAL, Cairo.FontWeight.BOLD);
                    cr.set_font_size (16);
                    string conf_text = "%.1f%%".printf (confidence * 100);
                    Cairo.TextExtents extents;
                    cr.text_extents (conf_text, out extents);

                    int text_x = box_x + box_w / 2 - (int) (extents.width / 2);
                    int text_y = box_y + box_h + 25;

                    cr.set_source_rgba (0, 0, 0, 0.7);
                    cr.rectangle (text_x - 5, text_y - extents.height - 5, extents.width + 10, extents.height + 10);
                    cr.fill ();

                    cr.set_source_rgba (box_color.red, box_color.green, box_color.blue, 1.0);
                    cr.move_to (text_x, text_y);
                    cr.show_text (conf_text);
                }
            }

            // Draw status text at top
            if (status_text != "") {
                cr.select_font_face ("Sans", Cairo.FontSlant.NORMAL, Cairo.FontWeight.BOLD);
                cr.set_font_size (20);
                Cairo.TextExtents extents;
                cr.text_extents (status_text, out extents);
                int text_x = width / 2 - (int) (extents.width / 2);
                int text_y = 30;

                cr.set_source_rgba (0, 0, 0, 0.7);
                cr.rectangle (text_x - 10, text_y - extents.height - 5, extents.width + 20, extents.height + 15);
                cr.fill ();

                cr.set_source_rgb (1.0, 1.0, 1.0);
                cr.move_to (text_x, text_y);
                cr.show_text (status_text);
            }
        }
    }
}