"""
mavDynamics 
    - this file implements the dynamic equations of motion for MAV
    - use unit quaternion for the attitude state
    
mavsim_python
    - Beard & McLain, PUP, 2012
    - Update history:  
        2/24/2020 - RWB
"""
import numpy as np
from models.mav_dynamics import MavDynamics as MavDynamicsForces
# load message types
from message_types.msg_state import MsgState
from message_types.msg_delta import MsgDelta
import parameters.aerosonde_parameters as MAV
from tools.rotations import quaternion_to_rotation, quaternion_to_euler, euler_to_rotation, euler_to_quaternion
import parameters.sensor_parameters as SENSOR
from message_types.msg_sensors import MsgSensors
import numpy as np


class MavDynamics(MavDynamicsForces):
    def __init__(self, Ts):
        super().__init__(Ts)
        # store wind data for fast recall since it is used at various points in simulation
        self._wind = np.array([[0.], [0.], [0.]])  # wind in NED frame in meters/sec
        # store forces to avoid recalculation in the sensors function
        self._forces = np.array([[0.], [0.], [0.]])
        self.initialize_velocity(MAV.u0, 0., 0.)
        
        # initialize the sensors message
        self._sensors = MsgSensors()
        # random walk parameters for GPS
        self._gps_eta_n = 0.
        self._gps_eta_e = 0.
        self._gps_eta_h = 0.
        # timer so that gps only updates every ts_gps seconds
        self._t_gps = 999.  # large value ensures gps updates at initial time.
        self.current_forces_moments = [0, 0, 0, 0, 0, 0]

        
    def initialize_velocity(self, Va, alpha, beta):
        self._Va = Va
        self._alpha = alpha
        self._beta = beta
        self._state[3] = Va*np.cos(alpha) * np.cos(beta)
        self._state[4] = Va*np.sin(beta)
        self._state[5] = Va*np.sin(alpha) * np.cos(beta)
        # update velocity data and forces and moments
        self._update_velocity_data()
        self._forces_moments(delta=MsgDelta())
        # update the message class for the true state
        self._update_true_state()        
        
    def calculate_trim_output(self, x):
        alpha, elevator, throttle = x
        
        phi, theta, psi = quaternion_to_euler(self._state[6:10]) # do i use theta?
        self._state[6:10] = euler_to_quaternion(phi, alpha, psi)
        
        self.initialize_velocity(self._Va, alpha, self._beta)
        delta = MsgDelta()
        delta.elevator = elevator
        delta.throttle = throttle
        forces = self._forces_moments(delta=delta)
        return(forces[0]**2 + forces[2]**2 + forces[4]**2)
            


    ###################################
    # public functions
    def sensors(self):
        "Return value of sensors on MAV: gyros, accels, absolute_pressure, dynamic_pressure, GPS"
       
        # simulate rate gyros(units are rad / sec)
        sensor_noise = np.radians(0.5)
        self._sensors.gyro_x = self.true_state.p + np.random.normal(0, sensor_noise)
        self._sensors.gyro_y = self.true_state.q + np.random.normal(0, sensor_noise)
        self._sensors.gyro_z = self.true_state.r + np.random.normal(0, sensor_noise)

        # simulate accelerometers(units of g)
        self._sensors.accel_x = self.current_forces_moments[0][0] / MAV.mass + MAV.gravity * np.sin(self.true_state.theta) + np.random.normal(0, SENSOR.accel_sigma)
        self._sensors.accel_y = self.current_forces_moments[1][0] / MAV.mass - MAV.gravity * np.cos(self.true_state.theta) * np.sin(self.true_state.phi) + np.random.normal(0, SENSOR.accel_sigma)
        self._sensors.accel_z = self.current_forces_moments[2][0] / MAV.mass - MAV.gravity * np.cos(self.true_state.theta) * np.cos(self.true_state.phi) + np.random.normal(0, SENSOR.accel_sigma)

        # simulate magnetometers
        # magnetic field in provo has magnetic declination of 12.5 degrees
        # and magnetic inclination of 66 degrees
        inc = np.deg2rad(66)
        dec = np.deg2rad(2.13)
        
        rotMatrix = (euler_to_rotation(phi=0, theta=-inc, psi=dec)).T
        
        # multiply e1
        mi = rotMatrix * (np.matrix([[1.00],[0.0],[0.0]]))
        
        mb = ((euler_to_rotation(phi=self.true_state.phi, psi=self.true_state.psi, theta=self.true_state.theta)).T) * mi
        
        self._sensors.mag_x = mb[0] * np.random.normal(0, SENSOR.mag_sigma)
        self._sensors.mag_y = mb[1] * np.random.normal(0, SENSOR.mag_sigma)
        self._sensors.mag_z = mb[2] * np.random.normal(0, SENSOR.mag_sigma)
                
        # simulate pressure sensors
        self._sensors.abs_pressure = 101325 * (1 - ((-0.0065*self.true_state.altitude)/288.15))**((MAV.gravity * 0.0289644)/(8.31432*-0.0065)) + np.random.normal(0, SENSOR.abs_pres_sigma)
        self._sensors.diff_pressure = ((MAV.rho*self.true_state.Va**2)/2) + np.random.normal(0, SENSOR.diff_pres_sigma)
        
        # simulate GPS sensor
        if self._t_gps >= SENSOR.ts_gps:
            self._gps_eta_n = np.exp(-SENSOR.gps_k*SENSOR.ts_gps)*self._gps_eta_n + np.random.normal(0, SENSOR.gps_n_sigma)*SENSOR.ts_gps
            self._gps_eta_e = np.exp(-SENSOR.gps_k*SENSOR.ts_gps)*self._gps_eta_e + np.random.normal(0, SENSOR.gps_e_sigma)*SENSOR.ts_gps
            self._gps_eta_h = np.exp(-SENSOR.gps_k*SENSOR.ts_gps)*self._gps_eta_h + np.random.normal(0, SENSOR.gps_h_sigma)*SENSOR.ts_gps
            self._gps_eta_Vg = np.random.normal(0, SENSOR.gps_Vg_sigma)
            self._gps_eta_course = np.random.normal(0, SENSOR.gps_course_sigma)
            
            self._sensors.gps_n = self.true_state.north + self._gps_eta_n
            self._sensors.gps_e = self.true_state.east + self._gps_eta_e
            self._sensors.gps_h = self.true_state.altitude + self._gps_eta_h
            
            self._sensors.gps_Vg = np.sqrt((self.true_state.Va*np.cos(self.true_state.psi)+self.true_state.wn)**2 + (self.true_state.Va*np.sin(self.true_state.psi)+self.true_state.we)**2) + self._gps_eta_Vg
            self._sensors.gps_course = (np.arctan2(self.true_state.Va*np.sin(self.true_state.psi) + self.true_state.we, self.true_state.Va*np.cos(self.true_state.psi)+self.true_state.wn) + self._gps_eta_course)
            self._t_gps += self._ts_simulation
        else:
            self._t_gps += self._ts_simulation
        return self._sensors

    def update(self, delta, wind):
        '''
            Integrate the differential equations defining dynamics, update sensors
            delta = (delta_a, delta_e, delta_r, delta_t) are the control inputs
            wind is the wind vector in inertial coordinates
            Ts is the time step between function calls.
        '''
        # get forces and moments acting on rigid bod
        forces_moments = self._forces_moments(delta)
        super()._rk4_step(forces_moments)
        # update the airspeed, angle of attack, and side slip angles using new state
        self._update_velocity_data(wind)
        # update the message class for the true state
        self._update_true_state()

    ###################################
    # private functions
    def _update_velocity_data(self, wind=np.zeros((6,1))):
        steady_state = wind[0:3]
        gust = wind[3:6]
        Vg_b = self._state[3:6]

        ##### TODO #####
        # convert wind vector from world to body frame (self._wind = ?)
        Va_b = Vg_b - steady_state
        ur, vr, wr = Va_b[:,0]
        self._Va = np.linalg.norm(Va_b, axis=0)[0]
        
        #^ velocity vector relative to the airmass ([ur , vr, wr]= ?)

        #^ compute airspeed (self._Va = ?)

        # \/compute angle of attack (self._alpha = ?)
        
        self._alpha = np.arctan2(wr, ur)
        
        # compute sideslip angle (self._beta = ?)
        
        self._beta = np.arcsin(vr/self._Va)

    def _forces_moments(self, delta):
        """
        return the forces on the UAV based on the state, wind, and control surfaces
        :param delta: np.matrix(delta_a, delta_e, delta_r, delta_t)
        :return: Forces and Moments on the UAV np.matrix(Fx, Fy, Fz, Ml, Mn, Mm)
        """
        ##### TODO ######
        # extract states (phi, theta, psi, p, q, r)
        
        phi, theta, psi = quaternion_to_euler(self._state[6:10])
        self.true_state.p = self._state.item(10)
        self.true_state.q = self._state.item(11)
        self.true_state.r = self._state.item(12)
        

        # compute gravitational forces ([fg_x, fg_y, fg_z])

        fg_b = euler_to_rotation(phi, theta, psi).T @ [0, 0,MAV.mass*MAV.gravity]
        

        # compute Lift and Drag coefficients (CL, CD)
        
        M_minus = np.exp(-MAV.M * (self._alpha - MAV.alpha0))
        M_plus = np.exp(MAV.M * (self._alpha + MAV.alpha0))
        sigmoid = (1 + M_minus + M_plus) / ((1 + M_minus) * (1 + M_plus))
        
        CL = (1 - sigmoid) * (MAV.C_L_0 + MAV.C_L_alpha * self._alpha) + sigmoid * (2 * np.sign(self._alpha) * np.sin(self._alpha)**2*np.cos(self._alpha))
        CD = MAV.C_D_p + (MAV.C_L_0 + MAV.C_L_alpha * self._alpha)**2 / (np.pi * MAV.e * MAV.AR)

        # compute Lift and Drag Forces (F_lift, F_drag)
        
        q_bar = 0.5 * MAV.rho * self._Va**2
        F_lift = q_bar * MAV.S_wing * (CL + MAV.C_L_delta_e * delta.elevator)

        # propeller thrust and torque
        thrust_prop, torque_prop = self._motor_thrust_torque(self._Va, delta.throttle)

        # compute longitudinal forces in body frame (fx, fz)
        Cx = -CD * np.cos(self._alpha) + CL * np.sin(self._alpha)
        Cxq = -MAV.C_D_q * np.cos(self._alpha) + MAV.C_L_q * np.sin(self._alpha)
        C_X_delta_e = -MAV.C_D_delta_e * np.cos(self._alpha) + MAV.C_L_delta_e * np.sin(self._alpha)

        fx = fg_b[0] + q_bar * MAV.S_wing * (Cx + Cxq * (MAV.c / (2*self._Va)) * self.true_state.q) + q_bar * MAV.S_wing * (C_X_delta_e * delta.elevator) + thrust_prop
        
        Cz = -CD * np.sin(self._alpha) - CL * np.cos(self._alpha)
        Czq = -MAV.C_D_q * np.sin(self._alpha) - MAV.C_L_q * np.cos(self._alpha)
        Cz_delta_e = -MAV.C_D_delta_e * np.sin(self._alpha) - MAV.C_L_delta_e * np.cos(self._alpha)
        
        fz = fg_b[2] + q_bar * MAV.S_wing * (Cz + Czq * ((self.true_state.q * MAV.c)/(2 * self._Va)) + Cz_delta_e * delta.elevator)


        # compute lateral forces in body frame (fy)
        
        fy = fg_b[1] + q_bar * MAV.S_wing * (MAV.C_Y_0 + MAV.C_Y_beta * self._beta + (MAV.C_Y_p *self.true_state.p * MAV.b)/(2 * self._Va) + (MAV.C_Y_r * MAV.b * self.true_state.r)/(2 * self._Va)) + q_bar * MAV.S_wing * (MAV.C_Y_delta_a * delta.aileron + MAV.C_Y_delta_r * delta.rudder)

        # compute logitudinal torque in body frame (My)
        
        l = q_bar * MAV.S_wing * MAV.b * (MAV.C_ell_0 + MAV.C_ell_beta * self._beta + MAV.C_ell_p * ((self.true_state.p * MAV.b)/(2 * self._Va)) + ((MAV.C_ell_r * self.true_state.r * MAV.b)/(2 * self._Va))) + q_bar * MAV.S_wing * MAV.b * ((MAV.C_ell_delta_a * delta.aileron) + MAV.C_ell_delta_r * delta.rudder) + torque_prop
                
        m = q_bar * MAV.S_wing * MAV.c * (MAV.C_m_0 + MAV.C_m_alpha * self._alpha + MAV.C_m_q *  ((self.true_state.q* MAV.c)/(2 * self._Va))) + q_bar * MAV.S_wing * MAV.c * (MAV.C_m_delta_e * delta.elevator)

        n = q_bar * MAV.S_wing * MAV.b * ((MAV.C_n_0 + MAV.C_n_beta * self._beta) + MAV.C_n_p * ((self.true_state.p * MAV.b)/(2 * self._Va)) + MAV.C_n_r * ((self.true_state.r * MAV.b)/(2 * self._Va))) + q_bar * MAV.S_wing * MAV.b * (MAV.C_n_delta_a * delta.aileron + MAV.C_n_delta_r * delta.rudder)
        # compute lateral torques in body frame (Mx, Mz)

        forces_moments = np.array([[fx, fy, fz, l, m, n]]).T
        self.current_forces_moments = forces_moments
        return forces_moments

    def _motor_thrust_torque(self, Va, delta_t):
        # compute thrust and torque due to propeller
        ##### TODO #####
        # map delta_t throttle command(0 to 1) into motor input voltage
        # TODO for sure, this is probably busted
        # v_in = MAV.V_max * delta_t
        
        # a = MAV.C_Q0 * MAV.rho * np.power(MAV.D_prop, 5) / ((2.*np.pi)**2)
        
        # b = (MAV.C_Q1 * MAV.rho * np.power(MAV.D_prop, 4) / (2.*np.pi)) * self._Va + MAV.KQ**2 / MAV.R_motor
        
        # c = MAV.C_Q2 * MAV.rho * np.power(MAV.D_prop, 3) * self._Va**2 - (MAV.KQ / MAV.R_motor) * v_in + MAV.KQ * MAV.i0

        # # Angular speed of propeller (omega_p = ?)
        # Omega_op = (-b + np.sqrt(b**2 - 4*a*c))/(2*a)
        
        # J_op = 2* np.pi * self._Va / (Omega_op * MAV.D_prop)
        
        # C_T = MAV.C_T2 * J_op**2 + MAV.C_T1 * J_op + MAV.C_T0
        # C_Q = MAV.C_Q2 * J_op**2 + MAV.C_Q1 * J_op + MAV.C_Q0
        
        # n = Omega_op / (2 * np.pi)
        # thrust_prop = MAV.rho * n**2 * np.power(MAV.D_prop, 4) * C_T
        # torque_prop = -MAV.rho * n**2 * np.power(MAV.D_prop, 5) * C_Q

        thrust_prop = 0.5 * MAV.rho * MAV.S_prop * ((MAV.k_motor * delta_t)**2 - Va**2)
        torque_prop = 0

        return thrust_prop, torque_prop

    def _update_true_state(self):
        # rewrite this function because we now have more information
        phi, theta, psi = quaternion_to_euler(self._state[6:10])
        pdot = quaternion_to_rotation(self._state[6:10]) @ self._state[3:6]
        self.true_state.north = self._state.item(0)
        self.true_state.east = self._state.item(1)
        self.true_state.altitude = -self._state.item(2)
        self.true_state.Va = self._Va
        self.true_state.alpha = self._alpha
        self.true_state.beta = self._beta
        self.true_state.phi = phi
        self.true_state.theta = theta
        self.true_state.psi = psi
        self.true_state.Vg = np.linalg.norm(pdot)
        self.true_state.gamma = np.arcsin(-pdot.item(2) / self.true_state.Vg)
        self.true_state.chi = np.arctan2(pdot.item(1), pdot.item(0))
        self.true_state.p = self._state.item(10)
        self.true_state.q = self._state.item(11)
        self.true_state.r = self._state.item(12)
        self.true_state.wn = self._wind.item(0)
        self.true_state.we = self._wind.item(1)
        self.true_state.bx = 0
        self.true_state.by = 0
        self.true_state.bz = 0
        self.true_state.camera_az = 0
        self.true_state.camera_el = 0
