# EHR memory workflow animation

`ehr_memory_workflow.mp4` is a silent, 49-second, 1920 × 1080, 24 fps video for a 16:9 presentation. In PowerPoint use **Insert → Video → Movie from File / This Device**, select the MP4, and choose **Start Automatically** if desired. Keynote and Google Slides can also use the MP4 through their normal movie/video insertion workflows (Google Slides requires uploading it to Drive).

`ehr_memory_workflow.gif` is a looping 960 × 540 alternative. The MP4 has sharper text and normal playback controls. `ehr_memory_workflow_poster.png` is a static overview. `step_01.png` through `step_07.png` are individual full-resolution stills; `storyboard.png` previews the sequence.

The animation depicts the repository's native EHR flow: parse historical logs, retrieve four examples by question similarity, collect old lessons through code neighbors before checking, generate per-example reasoning, make one joint consistency/safety judgment, use accepted examples plus historical warnings for knowledge/code generation, and retain rejected reasoning for future questions. A later rejection overwrites the same source record's lesson. The 51-original-plus-4-poisoned count describes the attack memory pool; the sample new question and accept/reject branches explain the mechanism rather than reporting per-question outcomes. Generated database code is not executed in this evaluation.

The editable source is `create_animation.py`. It requires Pillow and imageio-ffmpeg. It uses macOS Arial and Menlo font paths, which can be changed near the top of the script for another platform.
