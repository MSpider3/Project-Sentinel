/* SettingsView.vala - Configuration UI for Project Sentinel */

namespace Sentinel {

    public class SettingsView : Gtk.Box {
        private BackendService backend;

        // Camera
        private Gtk.DropDown camera_device_dropdown;
        private Gtk.SpinButton camera_width_spin;
        private Gtk.SpinButton camera_height_spin;
        private Gtk.SpinButton camera_fps_spin;

        // Face Detection
        private Gtk.SpinButton min_face_size_spin;

        // Liveness
        private Gtk.Scale spoof_threshold_scale;
        private Gtk.SpinButton challenge_timeout_spin;

        public SettingsView (BackendService backend) {
            Object (orientation: Gtk.Orientation.VERTICAL, spacing: 12);

            this.backend = backend;

            setup_ui ();
            load_configuration.begin ();
        }

        private void setup_ui () {
            set_margin_top (12);
            set_margin_bottom (12);
            set_margin_start (12);
            set_margin_end (12);

            // Header
            var title = new Gtk.Label ("Configuration");
            title.use_markup = true;
            title.halign = Gtk.Align.START;
            append (title);

            append (new Gtk.Separator (Gtk.Orientation.HORIZONTAL));

            // Create scrolled window for settings
            var scrolled = new Gtk.ScrolledWindow ();
            scrolled.vexpand = true;

            var settings_box = new Gtk.Box (Gtk.Orientation.VERTICAL, 20);
            scrolled.set_child (settings_box);
            append (scrolled);

            // Camera Settings
            settings_box.append (create_section_header ("Camera Settings (Auto-Detect enabled by default)"));
            settings_box.append (create_camera_settings ());

            // Face Detection
            settings_box.append (create_section_header ("Face Detection"));
            settings_box.append (create_facedetection_settings ());

            // Liveness Detection
            settings_box.append (create_section_header ("Liveness & Security"));
            settings_box.append (create_liveness_settings ());

            // Buttons
            var button_box = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 12);
            button_box.halign = Gtk.Align.CENTER;

            var save_button = new Gtk.Button.with_label ("Save Configuration");
            save_button.add_css_class ("suggested-action");
            save_button.clicked.connect (on_save_clicked);
            button_box.append (save_button);

            var reset_button = new Gtk.Button.with_label ("Reset to Defaults");
            reset_button.add_css_class ("destructive-action");
            reset_button.clicked.connect (on_reset_clicked);
            button_box.append (reset_button);

            append (button_box);
        }

        private Gtk.Widget create_section_header (string title) {
            var label = new Gtk.Label ("<b>%s</b>".printf (title));
            label.use_markup = true;
            label.halign = Gtk.Align.START;
            label.add_css_class ("title-3");
            return label;
        }

        private Gtk.Widget create_camera_settings () {
            var grid = new Gtk.Grid ();
            grid.column_spacing = 12;
            grid.row_spacing = 6;

            int row = 0;

            // Camera Device
            grid.attach (new Gtk.Label ("Camera Device ID:"), 0, row, 1, 1);
            string[] devices = { "0", "1", "2", "3" };
            camera_device_dropdown = new Gtk.DropDown.from_strings (devices);
            grid.attach (camera_device_dropdown, 1, row, 1, 1);
            row++;

            // Resolution Width
            grid.attach (new Gtk.Label ("Resolution Width:"), 0, row, 1, 1);
            camera_width_spin = new Gtk.SpinButton.with_range (320, 3840, 10);
            camera_width_spin.value = 640;
            grid.attach (camera_width_spin, 1, row, 1, 1);
            row++;

            // Resolution Height
            grid.attach (new Gtk.Label ("Resolution Height:"), 0, row, 1, 1);
            camera_height_spin = new Gtk.SpinButton.with_range (240, 2160, 10);
            camera_height_spin.value = 480;
            grid.attach (camera_height_spin, 1, row, 1, 1);
            row++;

            // FPS
            grid.attach (new Gtk.Label ("FPS:"), 0, row, 1, 1);
            camera_fps_spin = new Gtk.SpinButton.with_range (5, 60, 1);
            camera_fps_spin.value = 15;
            grid.attach (camera_fps_spin, 1, row, 1, 1);

            return grid;
        }

        private Gtk.Widget create_facedetection_settings () {
            var grid = new Gtk.Grid ();
            grid.column_spacing = 12;
            grid.row_spacing = 12;

            int row = 0;

            grid.attach (new Gtk.Label ("Min Face Size (pixels):"), 0, row, 1, 1);
            min_face_size_spin = new Gtk.SpinButton.with_range (50, 300, 5);
            min_face_size_spin.value = 100;
            grid.attach (min_face_size_spin, 1, row, 1, 1);
            row++;

            return grid;
        }

        private Gtk.Widget create_liveness_settings () {
            var grid = new Gtk.Grid ();
            grid.column_spacing = 12;
            grid.row_spacing = 12;

            int row = 0;

            // Spoof Threshold
            grid.attach (new Gtk.Label ("Anti-Spoof Strictness (0.0 = Off, 1.0 = Strict):"), 0, row, 1, 1);
            spoof_threshold_scale = new Gtk.Scale.with_range (Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.01);
            spoof_threshold_scale.set_value (0.92);
            spoof_threshold_scale.set_draw_value (true);
            spoof_threshold_scale.hexpand = true;
            grid.attach (spoof_threshold_scale, 1, row, 1, 1);
            row++;

            // Challenge Timeout
            grid.attach (new Gtk.Label ("Challenge Timeout (seconds):"), 0, row, 1, 1);
            challenge_timeout_spin = new Gtk.SpinButton.with_range (5, 60, 1);
            challenge_timeout_spin.value = 20;
            grid.attach (challenge_timeout_spin, 1, row, 1, 1);
            row++;

            return grid;
        }

        private async void load_configuration () {
            var result = yield backend.call_method ("get_config");

            if (result == null) {
                return;
            }

            var result_obj = result.get_object ();
            if (!result_obj.get_boolean_member ("success")) {
                return;
            }

            var config = result_obj.get_object_member ("config");

            // Load camera settings
            if (config.has_member ("camera_device_id")) {
                camera_device_dropdown.selected = (uint) config.get_int_member ("camera_device_id");
            }
            if (config.has_member ("camera_width")) {
                camera_width_spin.value = config.get_int_member ("camera_width");
            }
            if (config.has_member ("camera_height")) {
                camera_height_spin.value = config.get_int_member ("camera_height");
            }
            if (config.has_member ("camera_fps")) {
                camera_fps_spin.value = config.get_int_member ("camera_fps");
            }

            // Load FaceDetection
            if (config.has_member ("min_face_size")) {
                min_face_size_spin.value = config.get_int_member ("min_face_size");
            }

            // Load liveness
            if (config.has_member ("spoof_threshold")) {
                spoof_threshold_scale.set_value (config.get_double_member ("spoof_threshold"));
            }
            if (config.has_member ("challenge_timeout")) {
                challenge_timeout_spin.value = config.get_double_member ("challenge_timeout");
            }
        }

        private void on_save_clicked () {
            save_configuration.begin ();
        }

        private async void save_configuration () {
            var params = new Json.Object ();
            var config = new Json.Object ();

            // Camera settings
            config.set_int_member ("device_id", (int) camera_device_dropdown.selected);
            config.set_int_member ("camera_width", (int) camera_width_spin.value);
            config.set_int_member ("camera_height", (int) camera_height_spin.value);
            config.set_int_member ("camera_fps", (int) camera_fps_spin.value);

            // Face Detection
            config.set_int_member ("min_face_size", (int) min_face_size_spin.value);

            // Liveness settings
            config.set_double_member ("spoof_threshold", spoof_threshold_scale.get_value ());
            config.set_double_member ("challenge_timeout", challenge_timeout_spin.value);

            params.set_object_member ("config", config);

            var result = yield backend.call_method ("update_config", params);

            if (result != null) {
                var result_obj = result.get_object ();
                if (result_obj.get_boolean_member ("success")) {
                    show_toast ("Configuration saved securely!");
                } else {
                    show_toast ("Failed to save config.");
                }
            }
        }

        private void on_reset_clicked () {
            reset_configuration.begin ();
        }

        private async void reset_configuration () {
            var params = new Json.Object ();
            var result = yield backend.call_method ("reset_config", params);

            if (result != null) {
                var result_obj = result.get_object ();
                if (result_obj.get_boolean_member ("success")) {
                    show_toast ("Configuration reset to defaults.");
                    load_configuration.begin ();
                } else {
                    show_toast ("Failed to reset config.");
                }
            }
        }

        private void show_toast (string message) {
            // Find the parent Adw.ToastOverlay and show toast
            Gtk.Widget? parent = this.get_parent ();
            while (parent != null) {
                if (parent is Adw.ToastOverlay) {
                    var toast = new Adw.Toast (message);
                    toast.timeout = 3;
                    ((Adw.ToastOverlay) parent).add_toast (toast);
                    return;
                }
                parent = parent.get_parent ();
            }
            // Fallback if no ToastOverlay found
            var dialog = new Gtk.AlertDialog (message);
            dialog.show (null);
        }
    }
} // namespace Sentinel