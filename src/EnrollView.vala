/* EnrollView.vala - User enrollment view with multi-pose capture */

using Gtk;
using Gdk;
using GLib;
using Json;

namespace Sentinel {

    public class EnrollView : Gtk.Box {
        private BackendService backend;
        private CameraPreview camera_preview;
        private Gtk.Entry name_entry;
        private Gtk.CheckButton glasses_check;
        private Gtk.Button start_button;
        private Gtk.Button capture_button;
        private Gtk.Button stop_button;
        private Gtk.Overlay overlay;
        private Gtk.Box setup_box;
        private Gtk.Box capture_controls;
        private Gtk.Label hud_instruction;
        private Gtk.Label hud_status;
        private Gtk.ProgressBar hud_progress;
        public signal void enrollment_completed ();

        // Auto-capture logic
        private int ready_frame_counter = 0;
        private const int AUTO_CAPTURE_THRESHOLD = 125; // ~5+ seconds at standard FPS
        private bool is_waiting_for_user_ready = false;

        private bool is_enrolling = false;

        public EnrollView (BackendService backend) {
            GLib.Object (orientation: Gtk.Orientation.VERTICAL, spacing: 0);

            this.backend = backend;

            setup_ui ();
        }

        private void setup_ui () {
            // Modern UI: Use Overlay as main
            overlay = new Gtk.Overlay ();
            append (overlay);

            // 1. Setup Screen (Initial View)
            setup_box = new Gtk.Box (Gtk.Orientation.VERTICAL, 12);
            setup_box.halign = Gtk.Align.CENTER;
            setup_box.valign = Gtk.Align.CENTER;
            setup_box.set_margin_start (40);
            setup_box.set_margin_end (40);

            var title = new Gtk.Label ("<b>Enroll New User</b>");
            title.use_markup = true;
            title.add_css_class ("title-1");
            setup_box.append (title);

            var info_label = new Gtk.Label (
                                            "• Sit in a well-lit room\n"
                                            + "• Remove hats/masks\n"
                                            + "• You will be asked to hold several poses"
            );
            info_label.wrap = true;
            info_label.add_css_class ("dim-label");
            setup_box.append (info_label);

            var name_box = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 12);
            name_box.append (new Gtk.Label ("Name:"));
            name_entry = new Gtk.Entry ();
            name_entry.placeholder_text = "e.g., alex";
            name_box.append (name_entry);
            setup_box.append (name_box);

            glasses_check = new Gtk.CheckButton.with_label ("I wear glasses");
            setup_box.append (glasses_check);

            start_button = new Gtk.Button.with_label ("Start Enrollment");
            start_button.add_css_class ("suggested-action");
            start_button.add_css_class ("pill-button");
            start_button.clicked.connect (on_start_clicked);
            setup_box.append (start_button);

            // We append setup_box to the overlay but it will obscure camera if not careful.
            // Actually, we switch between showing setup_box and camera.
            // But GtkOverlay children are layered.
            // Better strategy: Main layout is Stack? Or just toggle visibility.
            // Let's hide camera preview initially? No, preview is nice.
            // Let's put setup_box in a ScrolledWindow or just center it over a blurred background?
            // For now, let's keep simple: Overlay contains Camera (always there but maybe hidden during setup)
            // OR we swap children.
            // Let's swap visible state.

            overlay.add_overlay (setup_box); // Overlay index 1

            // 2. Camera Preview (Background)
            camera_preview = new CameraPreview ();
            camera_preview.vexpand = true;
            camera_preview.hexpand = true;
            camera_preview.visible = false; // Hidden during setup
            overlay.set_child (camera_preview);

            // 3. Capture Controls (Overlay)
            capture_controls = new Gtk.Box (Gtk.Orientation.VERTICAL, 0);
            capture_controls.halign = Gtk.Align.FILL;
            capture_controls.valign = Gtk.Align.FILL;
            capture_controls.visible = false;

            // Top HUD
            var top_hud = new Gtk.Box (Gtk.Orientation.VERTICAL, 6);
            top_hud.valign = Gtk.Align.START;
            top_hud.halign = Gtk.Align.CENTER;
            top_hud.set_margin_top (40);

            hud_instruction = new Gtk.Label ("");
            hud_instruction.add_css_class ("hud-instruction"); // large text
            top_hud.append (hud_instruction);

            hud_status = new Gtk.Label ("Waiting...");
            hud_status.add_css_class ("hud-subtitle");
            top_hud.append (hud_status);

            hud_progress = new Gtk.ProgressBar ();
            hud_progress.set_margin_start (100);
            hud_progress.set_margin_end (100);
            top_hud.append (hud_progress);

            capture_controls.append (top_hud);

            // Bottom Controls
            var bottom_box = new Gtk.Box (Gtk.Orientation.HORIZONTAL, 12);
            bottom_box.valign = Gtk.Align.END;
            bottom_box.halign = Gtk.Align.CENTER;
            bottom_box.vexpand = true; // Push to bottom
            bottom_box.set_margin_bottom (30);

            capture_button = new Gtk.Button.with_label ("Capture Now");
            capture_button.add_css_class ("pill-button"); // Manual override
            capture_button.clicked.connect (on_capture_clicked);
            bottom_box.append (capture_button);

            stop_button = new Gtk.Button.with_label ("Cancel");
            stop_button.add_css_class ("pill-button-destructive");
            stop_button.clicked.connect (on_stop_clicked);
            bottom_box.append (stop_button);

            capture_controls.append (bottom_box);

            overlay.add_overlay (capture_controls);
        }

        private void on_start_clicked () {
            var name = name_entry.text.strip ().down ();
            if (name == "") {
                var dialog = new Gtk.AlertDialog ("Please enter your name");
                dialog.show (null);
                return;
            }

            start_enrollment.begin (name, glasses_check.active);
        }

        private async void start_enrollment (string user_name, bool wears_glasses) {
            var params = new Json.Object ();
            params.set_string_member ("user_name", user_name);
            params.set_boolean_member ("wears_glasses", wears_glasses);

            var result = yield backend.call_method ("start_enrollment", params);

            if (result == null) {
                var dialog = new Gtk.AlertDialog ("Failed to start enrollment");
                dialog.show (null);
                return;
            }

            var result_obj = result.get_object ();
            if (!result_obj.get_boolean_member ("success")) {
                var error = result_obj.get_string_member ("error");
                var dialog = new Gtk.AlertDialog ("Error: %s".printf (error));
                dialog.show (null);
                return;
            }

            // Switch to capture view
            setup_box.visible = false;
            camera_preview.visible = true;
            capture_controls.visible = true;
            is_enrolling = true;
            ready_frame_counter = 0;
            is_waiting_for_user_ready = true; // Wait for initial "Continue"
            capture_button.label = "Start First Pose";

            // Start serial async loop instead of timer
            run_enroll_loop.begin ();
        }

        // Serial async loop - waits for each frame to complete before requesting next
        private async void run_enroll_loop () {
            while (is_enrolling) {
                yield process_enroll_frame ();
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

        private async void process_enroll_frame () {
            var result = yield backend.call_method ("process_enroll_frame");

            if (result == null) {
                return;
            }

            var result_obj = result.get_object ();

            // Defensive check
            if (!result_obj.has_member ("success") || !result_obj.get_boolean_member ("success")) {
                return;
            }

            // Check if completed
            if (result_obj.has_member ("completed") && result_obj.get_boolean_member ("completed")) {
                string message = "";
                if (result_obj.has_member ("message") && !result_obj.get_null_member ("message")) {
                    message = result_obj.get_string_member ("message");
                }
                var dialog = new Gtk.AlertDialog (message);
                dialog.show (null);
                stop_enrollment.begin ();
                return;
            }

            // Defensive JSON parsing
            int current = 0;
            int total = 1;
            string instruction = "";
            string status = "";
            string frame_data = "";

            if (result_obj.has_member ("current_pose")) {
                current = (int) result_obj.get_int_member ("current_pose");
            }
            if (result_obj.has_member ("total_poses")) {
                total = (int) result_obj.get_int_member ("total_poses");
            }
            if (result_obj.has_member ("pose_info") && !result_obj.get_null_member ("pose_info")) {
                var pose_info = result_obj.get_object_member ("pose_info");
                if (pose_info.has_member ("instruction") && !pose_info.get_null_member ("instruction")) {
                    instruction = pose_info.get_string_member ("instruction");
                }
            }
            if (result_obj.has_member ("status") && !result_obj.get_null_member ("status")) {
                status = result_obj.get_string_member ("status");
            }
            if (result_obj.has_member ("frame") && !result_obj.get_null_member ("frame")) {
                frame_data = result_obj.get_string_member ("frame");
            }

            // Update UI
            hud_instruction.label = instruction;
            hud_progress.fraction = (double) current / (double) total;

            if (frame_data != "") {
                camera_preview.set_frame_from_base64 (frame_data);
            }

            // Face box
            if (result_obj.has_member ("face_box") && !result_obj.get_null_member ("face_box")) {
                var face_box = result_obj.get_array_member ("face_box");
                if (face_box != null && face_box.get_length () == 4) {
                    camera_preview.set_face_box (
                                                 (int) face_box.get_int_element (0),
                                                 (int) face_box.get_int_element (1),
                                                 (int) face_box.get_int_element (2),
                                                 (int) face_box.get_int_element (3)
                    );
                }
            } else {
                camera_preview.clear_face_box ();
            }

            // --- AUTO CAPTURE LOGIC ---
            if (is_waiting_for_user_ready) {
                hud_status.label = "Next position ready? (Click Continue)";
                camera_preview.set_box_color_from_string ("#888888"); // Grey
                return;
            }

            if (status == "ready") {
                ready_frame_counter++;
                camera_preview.set_box_color_from_string ("#00FF00"); // Green

                int remaining = AUTO_CAPTURE_THRESHOLD - ready_frame_counter;
                if (remaining > 0) {
                    int secs = (remaining / 15) + 1; // approx @ 15fps
                    hud_status.label = "Keep this pose: %d sec...".printf (secs);
                } else {
                    hud_status.label = "Capturing...";
                    capture_pose.begin (); // TRIGGER CAPTURE
                    ready_frame_counter = 0; // Reset
                }
            } else {
                ready_frame_counter = 0;
                camera_preview.set_box_color_from_string ("#FFFF00"); // Yellow
                hud_status.label = get_status_text (status);
            }
        }

        private string get_status_text (string status) {
            switch (status) {
            case "ready":
                return "Perfect! Hold still...";
            case "waiting":
                return "Looking for face...";
            case "no_face":
                return "No face detected";
            case "multiple_faces":
                return "Too many faces!";
            case "FACE_TOO_SMALL":
                return "Move closer to the camera";
            case "SPOOF_SUSPECTED":
                return "Anti-spoof error (Check lighting)";
            default:
                return status;
            }
        }

        private void on_capture_clicked () {
            if (is_waiting_for_user_ready) {
                is_waiting_for_user_ready = false;
                capture_button.label = "Capture Now";
                hud_status.label = "Perfect! Hold that position...";
                ready_frame_counter = 0;
                return;
            }
            capture_pose.begin ();
        }

        private async void capture_pose () {
            capture_button.sensitive = false;

            var result = yield backend.call_method ("capture_enroll_pose");

            if (result == null) {
                capture_button.sensitive = true;
                return;
            }

            var result_obj = result.get_object ();
            if (!result_obj.get_boolean_member ("success")) {
                var error = result_obj.get_string_member ("error");
                // Don't show dialog on auto-capture error, just reset
                hud_status.label = "Retry: %s".printf (error);
                capture_button.sensitive = true;
                ready_frame_counter = 0;
                return;
            }

            // Check if completed
            if (result_obj.get_boolean_member ("completed")) {
                var message = result_obj.get_string_member ("message");
                var dialog = new Gtk.AlertDialog (message);
                dialog.show (null);
                enrollment_completed ();
                stop_enrollment.begin ();
                return;
            }

            capture_button.sensitive = true;
            // Pose captured. Wait for user to transition to next pose.
            is_waiting_for_user_ready = true;
            capture_button.label = "Continue to next";
            ready_frame_counter = 0;
        }

        private void on_stop_clicked () {
            stop_enrollment.begin ();
        }

        private async void stop_enrollment () {
            is_enrolling = false;

            yield backend.call_method ("stop_enrollment");

            setup_box.visible = true;
            camera_preview.visible = false;
            camera_preview.clear_frame ();
            capture_controls.visible = false;

            name_entry.text = "";
            glasses_check.active = false;
        }
    }
} // namespace Sentinel