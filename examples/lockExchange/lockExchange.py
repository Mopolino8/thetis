# Lock Exchange Test case
# =======================
#
# Solves hydrostatic flow in a closed rectangular channel.
#
# Dianeutral mixing depends on mesh Reynolds number [1]
# Re_h = U dx / nu
# U = 0.5 m/s characteristic velocity ~ 0.5*sqrt(g_h drho/rho_0)
# dx = horizontal mesh size
# nu = background viscosity
#
#
# Smagorinsky factor should be C_s = 1/sqrt(Re_h)
#
# Mesh resolutions:
# - ilicak [1]:  dx =  500 m,  20 layers
# COMODO lock exchange benchmark [2]:
# - coarse:      dx = 2000 m,  10 layers
# - coarse2 (*): dx = 1000 m,  20 layers
# - medium:      dx =  500 m,  40 layers
# - medium2 (*): dx =  250 m,  80 layers
# - fine:        dx =  125 m, 160 layers
# (*) not part of the original benchmark
#
# [1] Ilicak et al. (2012). Spurious dianeutral mixing and the role of
#     momentum closure. Ocean Modelling, 45-46(0):37-58.
#     http://dx.doi.org/10.1016/j.ocemod.2011.10.003
# [2] COMODO Lock Exchange test.
#     http://indi.imag.fr/wordpress/?page_id=446
# [3] Petersen et al. (2015). Evaluation of the arbitrary Lagrangian-Eulerian
#     vertical coordinate method in the MPAS-Ocean model. Ocean Modelling,
#     86:93-113.
#     http://dx.doi.org/10.1016/j.ocemod.2014.12.004
#
# Tuomas Karna 2015-03-03

from thetis import *
from diagnostics import *
from plotting import *

# TODO implement front location callback DONE
# TODO implement runtime plotting DONE
# TODO add option to use constant viscosity or smag scheme DONE
# TODO implement automatic dt estimation for v_adv
# TODO test effect/necessity of lax_friedrichs
# TODO test computing smag nu with weak form uv gradients
# TODO add option for changing time integrator?
# TODO also plot u and w at runtime


def run_lockexchange(reso_str='coarse', poly_order=1, element_family='dg-dg',
                     reynolds_number=1.0, use_limiter=True, dt=None,
                     viscosity='const'):
    """
    Runs lock exchange problem with a bunch of user defined options.
    """
    comm = COMM_WORLD

    print_output('Running lock exchange problem with options:')
    print_output('Resolution: {:}'.format(reso_str))
    print_output('Element family: {:}'.format(element_family))
    print_output('Polynomial order: {:}'.format(poly_order))
    print_output('Reynolds number: {:}'.format(reynolds_number))
    print_output('Use slope limiters: {:}'.format(use_limiter))
    print_output('Number of cores: {:}'.format(comm.size))

    refinement = {'huge': 0.6, 'coarse': 1, 'coarse2': 2, 'medium': 4,
                  'medium2': 8, 'fine': 16, 'ilicak': 4}
    # set mesh resolution
    depth = 20.0
    delta_x = 2000.0/refinement[reso_str]
    layers = int(round(10*refinement[reso_str]))
    if reso_str == 'ilicak':
        layers = 20
    delta_z = depth/layers
    print_output('Mesh resolution dx={:} nlayers={:} dz={:}'.format(delta_x, layers, delta_z))

    # generate unit mesh and transform its coords
    x_max = 32.0e3
    x_min = -32.0e3
    n_x = (x_max - x_min)/delta_x
    mesh2d = UnitSquareMesh(n_x, 2)
    coords = mesh2d.coordinates
    # x in [x_min, x_max], y in [-dx, dx]
    coords.dat.data[:, 0] = coords.dat.data[:, 0]*(x_max - x_min) + x_min
    coords.dat.data[:, 1] = coords.dat.data[:, 1]*2*delta_x - delta_x

    nnodes = mesh2d.topology.num_vertices()
    ntriangles = mesh2d.topology.num_cells()
    nprisms = ntriangles*layers
    print_output('Number of 2D nodes={:}, triangles={:}, prisms={:}'.format(nnodes, ntriangles, nprisms))

    lim_str = '_lim' if use_limiter else ''
    dt_str = '_dt{:}'.format(dt) if dt is not None else ''
    options_str = '_'.join([reso_str,
                            element_family,
                            'p{:}'.format(poly_order),
                            'visc-{:}'.format(viscosity),
                            'Re{:}'.format(reynolds_number),
                            ]) + lim_str + dt_str
    outputdir = 'outputs_' + options_str
    print_output('Exporting to {:}'.format(outputdir))

    # temperature and salinity, for linear eq. of state (from Petersen, 2015)
    temp_left = 5.0
    temp_right = 30.0
    salt_const = 35.0
    rho_0 = 1000.0
    physical_constants['rho0'].assign(rho_0)

    # compute horizontal viscosity
    uscale = 0.5
    nu_scale = uscale * delta_x / reynolds_number
    print_output('Horizontal viscosity: {:}'.format(nu_scale))

    dt_adv = 1.0/20.0*delta_x/np.sqrt(2)/1.0
    dt_visc = 1.0/120.0*(delta_x/np.sqrt(2))**2/nu_scale
    print_output('Max dt for advection: {:}'.format(dt_adv))
    print_output('Max dt for viscosity: {:}'.format(dt_visc))

    t_end = 25 * 3600
    t_export = 15*60.0
    if dt is None:
        # take smallest stable dt that fits the export intervals
        max_dt = min(dt_adv, dt_visc)
        ntime = int(np.ceil(t_export/max_dt))
        dt = t_export/ntime

    # bathymetry
    p1_2d = FunctionSpace(mesh2d, 'CG', 1)
    bathymetry_2d = Function(p1_2d, name='Bathymetry')
    bathymetry_2d.assign(depth)

    # create solver
    solver_obj = solver.FlowSolver(mesh2d, bathymetry_2d, layers)
    options = solver_obj.options
    options.order = poly_order
    options.element_family = element_family
    options.solve_salt = False
    options.constant_salt = Constant(salt_const)
    options.solve_temp = True
    options.solve_vert_diffusion = False
    options.use_bottom_friction = False
    options.use_ale_moving_mesh = False
    # options.use_imex = True
    # options.use_semi_implicit_2d = False
    # options.use_mode_split = False
    options.baroclinic = True
    options.uv_lax_friedrichs = Constant(1.0)
    options.tracer_lax_friedrichs = Constant(1.0)
    options.salt_jump_diff_factor = None  # Constant(1.0)
    options.salt_range = Constant(5.0)
    options.use_limiter_for_tracers = use_limiter
    # To keep const grid Re_h, viscosity scales with grid: nu = U dx / Re_h
    if viscosity == 'smag':
        options.smagorinsky_factor = Constant(1.0/np.sqrt(reynolds_number))
    elif viscosity == 'const':
        options.h_viscosity = Constant(nu_scale)
    else:
        raise Exception('Unknow viscosity type {:}'.format(viscosity))
    options.v_viscosity = Constant(1e-4)
    options.h_diffusivity = None
    options.dt = dt
    # if options.use_mode_split:
    #     options.dt = dt
    options.t_export = t_export
    options.t_end = t_end
    options.outputdir = outputdir
    options.u_advection = Constant(1.0)
    options.check_vol_conservation_2d = True
    options.check_vol_conservation_3d = True
    options.check_temp_conservation = True
    options.check_temp_overshoot = True
    options.fields_to_export = ['uv_2d', 'elev_2d', 'uv_3d',
                                'w_3d', 'w_mesh_3d', 'temp_3d', 'density_3d',
                                'uv_dav_2d', 'uv_dav_3d', 'baroc_head_3d',
                                'baroc_head_2d', 'smag_visc_3d']
    options.fields_to_export_hdf5 = list(options.fields_to_export)
    options.equation_of_state = 'linear'
    options.lin_equation_of_state_params = {
        'rho_ref': rho_0,
        's_ref': 35.0,
        'th_ref': 5.0,
        'alpha': 0.2,
        'beta': 0.0,
    }

    # Use direct solver for 2D
    # options.solver_parameters_sw = {
    #     'ksp_type': 'preonly',
    #     'pc_type': 'lu',
    #     'pc_factor_mat_solver_package': 'mumps',
    #     'snes_monitor': False,
    #     'snes_type': 'newtonls',
    # }

    if comm.size == 1:
        rpe_calc = RPECalculator(solver_obj)
        rpe_callback = rpe_calc.export
        front_calc = FrontLocationCalculator(solver_obj)
        front_callback = front_calc.export
        plotter = Plotter(solver_obj, imgdir=solver_obj.options.outputdir + '/plots')
        plot_callback = plotter.export
    else:
        rpe_callback = None

    def callback():
        if comm.size == 1:
            rpe_callback()
            front_callback()
            plot_callback()

    solver_obj.create_equations()
    esize = solver_obj.fields.h_elem_size_2d
    min_elem_size = comm.allreduce(np.min(esize.dat.data), op=MPI.MIN)
    max_elem_size = comm.allreduce(np.max(esize.dat.data), op=MPI.MAX)
    print_output('Elem size: {:} {:}'.format(min_elem_size, max_elem_size))

    temp_init3d = Function(solver_obj.function_spaces.H, name='initial temperature')
    # vertical barrier
    # temp_init3d.interpolate(Expression('(x[0] > 0.0) ? v_r : v_l',
    #                                    v_l=temp_left, v_r=temp_right))
    # smooth condition
    temp_init3d.interpolate(Expression('v_l - (v_l - v_r)*0.5*(tanh(x[0]/sigma) + 1.0)',
                                       sigma=10.0, v_l=temp_left, v_r=temp_right))

    solver_obj.assign_initial_conditions(temp=temp_init3d)
    solver_obj.iterate(export_func=callback)


def get_argparser():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('-r', '--reso_str', type=str,
                        help='mesh resolution string',
                        default='coarse',
                        choices=['huge', 'coarse', 'coarse2', 'medium', 'medium2', 'fine', 'ilicak'])
    parser.add_argument('--no-limiter', action='store_false', dest='use_limiter',
                        help='do not use slope limiter for tracers')
    parser.add_argument('-p', '--poly_order', type=int, default=1,
                        help='order of finite element space')
    parser.add_argument('-f', '--element-family', type=str,
                        help='finite element family', default='dg-dg')
    parser.add_argument('-re', '--reynolds-number', type=float, default=1.0,
                        help='mesh Reynolds number for Smagorinsky scheme')
    parser.add_argument('-dt', '--dt', type=float,
                        help='force value for 3D time step')
    parser.add_argument('-visc', '--viscosity', type=str,
                        help='Type of horizontal viscosity',
                        default='const',
                        choices=['const', 'smag'])
    return parser


def parse_options():
    parser = get_argparser()
    args = parser.parse_args()
    args_dict = vars(args)
    run_lockexchange(**args_dict)

if __name__ == '__main__':
    parse_options()
