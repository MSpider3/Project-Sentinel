/* AuthView.vala - Authentication view with camera preview and liveness checks */

using Gtk;
using Gdk;
using GLib;
using Json;

namespace Sentinel {

    public class AuthView : Gtk.Box {
        private BackendService backend;
        private CameraPreview camera_preview;
        private Gtk.Label status_label;
        private Gtk.Label prompt_label;
        private Gtk.DropDown user_combo;
        private Gtk.Button start_button;
        private Gtk.Button stop_button;
        private Gtk.Overlay overlay;
        private Gtk.Box controls_overlay;

        private bool is_running = false;

        // Thresholds (defaults, will update from config)
        private double golden_thresh = 0.25;
        private double standard_thresh = 0.42;
        private double twofa_thresh = 0.50;

        public AuthView (BackendService backend) {
            GLib.Object (orientation: Gtk.Orientation.VERTICAL, spacing: 0);
            this.vexpand = true;
            this.hexpand = true;
            this.valign = Gtk.Align.FILL;
            this.halign = Gtk.Align.FILL;

            this.backend = backend;

            setup_ui ();
        }

        // Call this after the backend has successfully started+initialized.
        public void on_backend_ready () {
            load_config.begin ();
            user_combo.sensitive = true;
        }

        private async void load_config () {
            var result = yield backend.call_method ("get_config");

            if (result != null) {
                var obj = result.get_object ();
                if (obj.get_boolean_member ("success")) {
                    var cfg = obj.get_object_member ("config");
                    if (cfg.has_member ("golden_threshold"))golden_thresh = cfg.get_double_member ("golden_threshold");
                    if (cfg.has_member ("standard_threshold"))standard_thresh = cfg.get_double_member ("standard_threshold");
                    if (cfg.has_member ("twofa_threshold"))twofa_thresh = cfg.get_double_member ("twofa_threshold");
                }
            }
        }

        private void setup_ui () {
            // Modern UI: Use Overlay as the main container
            overlay = new Gtk.Overlay ();
            overlay.vexpand = true;
            overlay.hexpand = true;
            overlay.valign = Gtk.Align.FILL;
            overlay.halign = Gtk.Align.FILL;
            overlay.set_size_request(640, 480);
            append (overlay);

            // 1. Camera Preview (Background)
            camera_preview = new CameraPreview ();
            camera_preview.vexpand = true;
            camera_preview.hexpand = true;
            camera_preview.visible = true;
            overlay.set_child (camera_preview);

            // 2. HUD Overlay (Status & Prompts) - Top Center
            var hud_box = new Gtk.Box (Gtk.Orientation.VERTICAL, 6);
            hud_box.halign = Gtk.Align.CENTER;
            hud_box.valign = Gtk.Align.START;
            hud_box.set_margin_top (40);

            status_label = new Gtk.Label ("Ready to authenticate");
            status_label.add_css_class ("hud-title");
            hud_box.append (status_label);

            prompt_label = new Gtk.Label ("");
            prompt_label.add_css_class ("hud-subtitle");
            hud_box.append (prompt_label);

            overlay.add_overlay (hud_box);

            // 3. Controls Overlay - Bottom
            controls_overlay = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 12);
            controls_overlay.halign = Gtk.Align.CENTER;
            controls_overlay.valign = Gtk.Align.END;
            controls_overlay.set_margin_bottom (30);
            controls_overlay.add_css_class ("glass-panel"); // defined in style.css
            controls_overlay.set_margin_start (20);
            controls_overlay.set_margin_end (20);

            // User selection
            var user_label = new Gtk.Label ("User:");
            controls_overlay.append (user_label);

            var user_model = new Gtk.StringList (null);
            user_model.append ("Auto-detect");
            user_combo = new Gtk.DropDown (user_model, null);
            controls_overlay.append (user_combo);

            // Buttons
            start_button = new Gtk.Button.with_label ("Start");
            start_button.add_css_class ("pill-button");
            start_button.clicked.connect (on_start_clicked);
            controls_overlay.append (start_button);

            stop_button = new Gtk.Button.with_label ("Stop");
            stop_button.add_css_class ("pill-button-destructive");
            stop_button.clicked.connect (on_stop_clicked);
            stop_button.visible = false;
            controls_overlay.append (stop_button);

            overlay.add_overlay (controls_overlay);
        }

        public async void refresh_users () {
            var result = yield backend.call_method ("get_enrolled_users");

            if (result == null) {
                return;
            }

            var result_obj = result.get_object ();
            if (!result_obj.get_boolean_member ("success")) {
                return;
            }

            var users = result_obj.get_array_member ("users");
            // Refresh user list logic
            // Clear items? Gtk4 StringList doesn't clear easily without loop.
            // Ideally re-create model.
            var new_model = new Gtk.StringList (null);
            new_model.append ("Auto-detect");

            users.foreach_element ((arr, idx, node) => {
                new_model.append (node.get_string ());
            });

            user_combo.model = new_model;
            user_combo.selected = 0;
        }

        private void on_start_clicked () {
            start_authentication.begin ();
        }

        private async void start_authentication () {
            // Get selected user
            string? selected_user = null;
            if (user_combo.selected > 0) {
                var user_model = (Gtk.StringList) user_combo.model;
                selected_user = user_model.get_string (user_combo.selected);
            }

            // Call backend
            var params = new Json.Object ();
            if (selected_user != null) {
                params.set_string_member ("user", selected_user);
            }

            var result = yield backend.call_method ("start_authentication", params);

            if (result == null) {
                status_label.label = "Failed to start authentication";
                return;
            }

            var result_obj = result.get_object ();
            if (!result_obj.get_boolean_member ("success")) {
                var error = result_obj.get_string_member ("error");

                // --- SECURITY POLICY: EXPIRATION ---
                if (error == "BIOMETRICS_EXPIRED") {
                    var dialog = new Gtk.AlertDialog ("<b>Security Policy Alert:</b>\n\nYour biometric data has expired (older than 45 days).\nPlease re-register to continue using face unlock.");
                    dialog.show (null);
                    // Force stop
                    return;
                }

                status_label.label = "Error: %s".printf (error);
                return;
            }

            // Start processing loop
            is_running = true;
            start_button.visible = false;
            stop_button.visible = true;
            user_combo.sensitive = false;

            // Start serial async loop instead of timer to prevent IPC conflicts
            run_auth_loop.begin ();
        }

        // Serial async loop - waits for each frame to complete before requesting next
        private async void run_auth_loop () {
            while (is_running) {
                yield process_frame ();

                // Poll at ~15 FPS to respect daemon RPM rate limits
                yield nap (66);
            }
        }

        // Helper for async sleep
        private async void nap (uint interval_ms) {
            Timeout.add (interval_ms, () => {
                nap.callback ();
                return false;
            });
            yield;
        }

        private async void process_frame () {
            var result = yield backend.call_method ("process_auth_frame");

            if (result == null) {
                return;
            }

            var result_obj = result.get_object ();

            // Defensive check for success field
            if (!result_obj.has_member ("success") || !result_obj.get_boolean_member ("success")) {
                return;
            }

            // Defensive JSON parsing
            string state = "";
            if (result_obj.has_member ("state") && !result_obj.get_null_member ("state")) {
                state = result_obj.get_string_member ("state");
            }

            string message = "";
            if (result_obj.has_member ("message") && !result_obj.get_null_member ("message")) {
                message = result_obj.get_string_member ("message");
            }

            string frame_data = "";
            if (result_obj.has_member ("frame") && !result_obj.get_null_member ("frame")) {
                frame_data = result_obj.get_string_member ("frame");
            }

            // --- LIVE CONFIDENCE DISPLAY ---
            double dist = 1.0;
            if (result_obj.has_member ("info")) {
                var info = result_obj.get_object_member ("info");
                if (info.has_member ("dist") && !info.get_null_member ("dist")) {
                    dist = info.get_double_member ("dist");
                    // Calculate confidence %
                    double confidence = double.max (0.0, double.min (1.0, 1.0 - dist)) * 100.0;
                    camera_preview.set_confidence (confidence / 100.0);
                }
            }

            // Update UI Labels via HUD
            status_label.label = message;

            if (frame_data != "") {
                camera_preview.set_frame_from_base64 (frame_data);
            }

            // Face box & Tier Logic Visualization
            if (result_obj.has_member ("face_box") && !result_obj.get_null_member ("face_box")) {
                var face_box = result_obj.get_array_member ("face_box");
                if (face_box != null && face_box.get_length () == 4) {
                    camera_preview.set_face_box (
                                                 (int) face_box.get_int_element (0),
                                                 (int) face_box.get_int_element (1),
                                                 (int) face_box.get_int_element (2),
                                                 (int) face_box.get_int_element (3)
                    );

                    // Set color based on State
                    if (state == "STATE_BLOCKED_INTRUDER") {
                        camera_preview.set_box_color_from_string ("#FF0000"); // Red
                        status_label.label = "ACCESS DENIED: INTRUDER DETECTED";
                    } else {
                        camera_preview.set_box_color_by_status (state);
                    }
                }
            } else {
                camera_preview.clear_face_box ();
            }

            // Check for terminal states
            if (state == "SUCCESS") {
                // Check Tier
                if (dist < golden_thresh) {
                    status_label.label = "Access Granted (Tier 1: High Security)";
                    check_intruder_review.begin (); // Trigger review
                } else if (dist < standard_thresh) {
                    status_label.label = "Access Granted (Tier 2: Standard)";
                } else {
                    // Should theoretically be 2FA or fail, but if success happened:
                    status_label.label = "Access Granted";
                }

                yield nap (1500); // Show success briefly

                stop_authentication.begin ();
            } else if (state == "FAILURE" || state == "LOCKOUT") {
                yield nap (2000);

                stop_authentication.begin ();
            } else if (state == "REQUIRE_2FA") {
                // Keep running, prompt user
                prompt_label.label = "Tier 3: 2FA Required - Please Blink";
            }
        }

        private async void check_intruder_review () {
            // Only runs on Tier 1 (Golden) Success
            var dialog = new IntrusionReviewDialog ((Gtk.Window) this.get_root (), backend);
            dialog.show ();
            // We don't block here, just show it.
        }

        private void on_stop_clicked () {
            stop_authentication.begin ();
        }

        private async void stop_authentication () {
            is_running = false;

            yield backend.call_method ("stop_authentication");

            start_button.visible = true;
            stop_button.visible = false;
            user_combo.sensitive = true;
            status_label.label = "Authentication stopped";
            prompt_label.label = "";
            camera_preview.clear_face_box ();
            camera_preview.clear_frame ();
            camera_preview.set_confidence (0.0);
        }
    }
} // namespace Sentinel