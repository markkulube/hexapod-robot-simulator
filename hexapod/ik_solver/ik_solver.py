# Please look at the discussion of the Inverse Kinematics algorithm
# As detailed in the README of this directory

from settings import ASSERTION_ENABLED, ALPHA_MAX_ANGLE
import numpy as np
from copy import deepcopy
from hexapod.ik_solver.helpers import (
    body_contact_shoved_on_ground,
    legs_too_short,
    beta_gamma_not_within_range,
    angle_above_limit,
    might_sanity_leg_lengths_check,
    might_sanity_beta_gamma_check,
    might_print_ik,
    might_print_points,
)
from hexapod.const import HEXAPOD_POSE
from hexapod.points import (
    Point,
    length,
    add_vectors,
    scalar_multiply,
    vector_from_to,
    get_unit_vector,
    is_triangle,
    project_vector_onto_plane,
    angle_between,
    angle_opposite_of_last_side,
)
from hexapod.ik_solver.shared import (
    update_hexapod_points,
    find_twist_frame,
    compute_twist_wrt_to_world,
)


# This function computes the joint angles required to
# rotate and translate the hexapod given the parameters given
# - rot_x, rot_y, rot_z are how the hexapod should be rotated
# - percent_x, percent_y, percent_z are the percent shift of the
# center of gravity of the hexapod.
#
# Where the hexapod contacts the ground at its initial state
# will also be the final points of contact by the hexapod and the ground
# unless the leg can't reach it.
#
# ❗❗❗IMPORTANT: The hexapod will be MODIFIED and returned along with
# a dictionary of POSES containing the 18 computed angles
# if the pose is impossible, the the function will output a
# hexapod whose body is detached from its legs, the body having the pose required
# an ALERT message will also be returned explaining why the pose is impossible
#
poses = deepcopy(HEXAPOD_POSE)


def inverse_kinematics_update(hexapod, ik_parameters):

    tx = ik_parameters["percent_x"] * hexapod.mid
    ty = ik_parameters["percent_y"] * hexapod.side
    tz = ik_parameters["percent_z"] * hexapod.tibia
    rotx, roty, rotz = (
        ik_parameters["rot_x"],
        ik_parameters["rot_y"],
        ik_parameters["rot_z"],
    )

    hexapod.update_stance(ik_parameters["hip_stance"], ik_parameters["leg_stance"])
    hexapod.detach_body_rotate_and_translate(rotx, roty, rotz, tx, ty, tz)

    if body_contact_shoved_on_ground(hexapod):
        raise Exception("Impossible at given height. \n body contact shoved on ground")

    x_axis = Point(1, 0, 0)
    legs_up_in_the_air = []

    for i in range(hexapod.LEG_COUNT):
        leg_name = hexapod.legs[i].name
        body_contact = hexapod.body.vertices[i]
        foot_tip = hexapod.legs[i].foot_tip()
        body_to_foot_vector = vector_from_to(body_contact, foot_tip)

        # find the coxia vector which is the vector
        # from body contact point to joint between coxia and femur limb
        projection = project_vector_onto_plane(body_to_foot_vector, hexapod.z_axis)
        unit_coxia_vector = get_unit_vector(projection)
        coxia_vector = scalar_multiply(unit_coxia_vector, hexapod.coxia)

        # coxia point / joint is the point connecting the coxia and tibia limbs
        coxia_point = add_vectors(body_contact, coxia_vector)
        if coxia_point.z < foot_tip.z:
            raise Exception("Impossible at given height. \n coxia joint shoved on ground")

        # *******************
        # 1. Compute p0, p1 and (possible) p3 wrt leg frame
        # *******************
        p0 = Point(0, 0, 0)
        p1 = Point(hexapod.coxia, 0, 0)

        # Find p3 aka foot tip (ground contact) with respect to the local leg frame
        rho = angle_between(unit_coxia_vector, body_to_foot_vector)
        body_to_foot_length = length(body_to_foot_vector)
        p3x = body_to_foot_length * np.cos(np.radians(rho))
        p3z = -body_to_foot_length * np.sin(np.radians(rho))
        p3 = Point(p3x, 0, p3z)

        # *******************
        # 2. Compute p2, beta, gamma and final p3 wrt leg frame
        # *******************

        # These values are needed to compute
        # p2 aka tibia joint (point between femur limb and tibia limb)
        coxia_to_foot_vector2d = vector_from_to(p1, p3)
        d = length(coxia_to_foot_vector2d)

        # If we can form this triangle this means we probably can reach the target ground contact point
        if is_triangle(hexapod.tibia, hexapod.femur, d):
            # .................................
            # CASE A: a triangle can be formed with
            # coxia to foot vector, hexapod's femur and tibia
            # .................................
            theta = angle_opposite_of_last_side(d, hexapod.femur, hexapod.tibia)
            phi = angle_between(coxia_to_foot_vector2d, x_axis)

            beta = theta - phi  # case 1 or 2
            if p3.z > 0:  # case 3
                beta = theta + phi

            z_ = hexapod.femur * np.sin(np.radians(beta))
            x_ = p1.x + hexapod.femur * np.cos(np.radians(beta))

            p2 = Point(x_, 0, z_)
            femur_vector = vector_from_to(p1, p2)
            tibia_vector = vector_from_to(p2, p3)
            gamma = 90 - angle_between(femur_vector, tibia_vector)

            if p2.z < p3.z:
                raise Exception(
                    f"Cannot reach target ground point. \n {leg_name} leg cannot reach it because the ground is blocking the path."
                )
        else:
            # .................................
            # CASE B: It's impossible to reach target ground point
            # .................................
            if d + hexapod.tibia < hexapod.femur:
                raise Exception(
                    f"Cannot reach target ground point. \n Femur length of {leg_name} leg is too long."
                )
            if d + hexapod.femur < hexapod.tibia:
                raise Exception(
                    f"Cannot reach target ground point. \n Tibia length of {leg_name} leg is too long."
                )

            # Then hexapod.femur + hexapod.tibia < d:
            legs_up_in_the_air.append(leg_name)
            LEGS_TOO_SHORT, alert_msg = legs_too_short(legs_up_in_the_air)
            if LEGS_TOO_SHORT:
                raise Exception(alert_msg)

            femur_tibia_direction = get_unit_vector(coxia_to_foot_vector2d)
            femur_vector = scalar_multiply(femur_tibia_direction, hexapod.femur)
            p2 = add_vectors(p1, femur_vector)
            tibia_vector = scalar_multiply(femur_tibia_direction, hexapod.tibia)
            p3 = add_vectors(p2, tibia_vector)

            # Find beta and gamma
            gamma = 0.0
            leg_x_axis = Point(1, 0, 0)
            beta = angle_between(leg_x_axis, femur_vector)
            if femur_vector.z < 0:
                beta = -beta

        # Final p1, p2, p3, beta and gamma computed at this point
        not_within_range, alert_msg = beta_gamma_not_within_range(beta, gamma, leg_name)
        if not_within_range:
            raise Exception(alert_msg)

        # *******************
        # 3. Compute alpha and twist_frame
        # Find frame used to twist the leg frame wrt to hexapod's body contact point's x axis
        # *******************
        alpha, twist_frame = find_twist_frame(hexapod, unit_coxia_vector)
        alpha = compute_twist_wrt_to_world(alpha, hexapod.body.COXIA_AXES[i])
        alpha_limit, alert_msg = angle_above_limit(
            alpha, ALPHA_MAX_ANGLE, leg_name, "coxia angle (alpha)"
        )

        if alpha_limit:
            raise Exception(alert_msg)

        # *******************
        # 4. Update hexapod points and finally update the pose
        # *******************
        points = [p0, p1, p2, p3]
        might_print_points(points, leg_name)

        # Convert points from local leg coordinate frame to world coordinate frame
        for point in points:
            point.update_point_wrt(twist_frame)
            if ASSERTION_ENABLED:
                assert hexapod.body_rotation_frame is not None, "No rotation frame!"
            point.update_point_wrt(hexapod.body_rotation_frame)
            point.move_xyz(body_contact.x, body_contact.y, body_contact.z)

        might_sanity_leg_lengths_check(hexapod, leg_name, points)
        might_sanity_beta_gamma_check(beta, gamma, leg_name, points)

        # Update hexapod's points to what we computed
        update_hexapod_points(hexapod, i, points)

        poses[i]["coxia"] = alpha
        poses[i]["femur"] = beta
        poses[i]["tibia"] = gamma

    might_print_ik(poses, ik_parameters, hexapod)
    return poses, hexapod